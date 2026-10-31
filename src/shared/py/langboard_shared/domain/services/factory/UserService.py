from datetime import datetime, timezone
from json import dumps as json_dumps
from json import loads as json_loads
from typing import TYPE_CHECKING, Any, Sequence, cast
from urllib.parse import urlparse
from ....core.caching import Cache
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.domain.BaseDomainService import TMutableValidatorMap
from ....core.resources.locales.LangEnum import LangEnum
from ....core.storage import FileModel
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TUserParam
from ....core.utils.Converter import convert_python_data
from ....core.utils.Encryptor import Encryptor
from ....core.utils.String import concat, generate_random_string
from ....Env import UI_QUERY_NAMES, Env
from ....helpers import InfraHelper
from ....publishers import AppSettingPublisher, UserPublisher
from ....security import Auth
from ...models import Organization, SettingRole, User, UserEmail, UserProfile, UserSignInHistory
from ...models.SettingRole import SettingRoleAction, SettingRoleCategory
from ...models.UserSignInHistory import SignInErrorCode


if TYPE_CHECKING:
    from .ProjectInvitationService import ProjectInvitationService


class UserOwnershipError(ValueError):
    """Account lifecycle changes require transferring existing organizations first."""


class UserService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "user"

    def get_by_id_like(self, user: TUserParam | None) -> User | None:
        if not user:
            return None
        # Account lifecycle must be current before settings or role mutations.
        with DbSession.use(readonly=False) as db:
            return db.exec(
                SqlBuilder.select.table(User)
                .where(User.id == InfraHelper.convert_id(user))
                .execution_options(populate_existing=True)
            ).first()

    def get_sign_in_history_in_settings(self, user_uid: str, **filters) -> dict | None:
        return self.repo.user_sign_in_history.get_page_in_settings(user_uid, **filters)

    def get_business_activity_in_settings(
        self,
        viewer: User,
        target: User,
        *,
        page: int = 1,
        limit: int = 20,
        project_uid: str | None = None,
        action: str | None = None,
        occurred_from: datetime | None = None,
        occurred_before: datetime | None = None,
    ) -> dict[str, Any]:
        """Return readable event metadata; exclude history even from SQL projections."""
        from sqlalchemy import String, literal, select, union_all
        from sqlalchemy import cast as sql_cast
        from ....core.security.CollaborationChannel import CollaborationChannel
        from ....infrastructure.repositories.factory.UserRepository import UserRepository
        from ...models import Card, Project, ProjectActivity, ProjectWiki, ProjectWikiActivity
        from ..CardVisibilityPolicy import project_activity_visibility_scope
        from .CardService import CardService

        if not viewer.is_admin or not self.can_search_all_users(viewer):
            raise PermissionError("User read permission required")
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError("Invalid pagination")
        contexts = self._get_service(CardService).resolve_work_visibility_contexts(viewer, CollaborationChannel.Api)
        if not contexts:
            return {"records": [], "page": page, "limit": limit, "has_more": False}
        queries = []
        for kind, model, resource, actions in (
            ("project", ProjectActivity, Card, UserRepository.BUSINESS_PROJECT_ACTIONS),
            ("wiki", ProjectWikiActivity, ProjectWiki, UserRepository.BUSINESS_WIKI_ACTIONS),
        ):
            query = (
                select(
                    model.id.label("event_id"),
                    model.created_at.label("occurred_at"),
                    sql_cast(model.activity_type, String).label("action"),
                    literal(kind).label("scope"),
                    Project.id.label("project_id"),
                    Project.title.label("project_title"),
                    resource.id.label("resource_id"),
                    resource.title.label("resource_title"),
                )
                .join(Project, Project.id == model.project_id)
                .where(
                    model.user_id == target.id,
                    model.bot_id.is_(None),
                    model.activity_type.in_(actions),
                    Project.deleted_at.is_(None),
                    Project.id.in_(list(contexts)),
                )
            )
            if kind == "project":
                query = query.outerjoin(Card, Card.id == ProjectActivity.card_id).where(
                    project_activity_visibility_scope(contexts),
                )
            else:
                # This method requires an administrator. Native can_view grants administrators
                # wiki visibility, while the project scope above still requires current access.
                query = query.join(ProjectWiki, ProjectWiki.id == model.project_wiki_id).where(
                    ProjectWiki.deleted_at.is_(None),
                    ProjectWiki.project_id == Project.id,
                )
            if project_uid:
                query = query.where(Project.id == InfraHelper.convert_id(project_uid))
            if action:
                query = query.where(model.activity_type == action)
            if occurred_from:
                query = query.where(model.created_at >= occurred_from)
            if occurred_before:
                query = query.where(model.created_at < occurred_before)
            queries.append(query)
        events = union_all(*queries).subquery()
        with DbSession.use(readonly=False) as db:
            rows = db.exec(
                select(events)
                .order_by(
                    events.c.occurred_at.desc(),
                    events.c.event_id.desc(),
                    events.c.scope,
                )
                .offset((page - 1) * limit)
                .limit(limit + 1)
            ).all()
        records = []
        for row in rows[:limit]:
            event_id, occurred_at, event_action, scope, project_id, project_title, resource_id, resource_title = row
            records.append(
                {
                    "event_id": f"{scope}:{SnowflakeID(event_id).to_short_code()}",
                    "occurred_at": occurred_at.replace(tzinfo=timezone.utc)
                    if occurred_at.tzinfo is None
                    else occurred_at,
                    "actor_principal_uid": target.get_uid(),
                    "action": event_action,
                    "project": {"uid": SnowflakeID(project_id).to_short_code(), "title": project_title},
                    "resource": {
                        "type": "wiki" if scope == "wiki" else "card",
                        "uid": SnowflakeID(resource_id).to_short_code(),
                        "title": resource_title,
                    }
                    if resource_id
                    else None,
                }
            )
        return {"records": records, "page": page, "limit": limit, "has_more": len(rows) > limit}

    def create_cache_name(self, cache_type: str, email: str) -> str:
        return f"{cache_type}:{email}"

    def get_api_list_in_settings(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        query: str = "",
        account_status: str = "all",
        column_filters: dict[str, str] | None = None,
        identity_provider: str = "all",
        admin_role: str = "all",
        exclude_user_uid: str | None = None,
        last_signed_in_from: datetime | None = None,
        last_signed_in_before: datetime | None = None,
        last_business_activity_from: datetime | None = None,
        last_business_activity_before: datetime | None = None,
        sort_by: str = "created_at",
        sort_direction: str = "desc",
        user_uid: str | None = None,
    ) -> list[dict[str, Any]]:
        users = self.repo.user.get_all_with_profile_and_activity_in_settings(
            offset=offset,
            limit=limit,
            query=query,
            account_status=account_status,
            column_filters=column_filters,
            identity_provider=identity_provider,
            admin_role=admin_role,
            exclude_user_uid=exclude_user_uid,
            last_signed_in_from=last_signed_in_from,
            last_signed_in_before=last_signed_in_before,
            last_business_activity_from=last_business_activity_from,
            last_business_activity_before=last_business_activity_before,
            sort_by=sort_by,
            sort_direction=sort_direction,
            user_uid=user_uid,
        )
        if not users:
            return []

        api_key_roles = self.repo.role.api_key.get_list(user_id=cast(SnowflakeID, [row[0].id for row in users]))
        api_key_role_actions_dicts = {role.user_id: role.actions for role in api_key_roles}

        setting_roles = self.repo.role.setting.get_list(user_id=cast(SnowflakeID, [row[0].id for row in users]))
        setting_role_actions_dicts = {role.user_id: role.actions for role in setting_roles}

        mcp_roles = self.repo.role.mcp.get_list(user_id=cast(SnowflakeID, [row[0].id for row in users]))
        mcp_role_actions_dicts = {role.user_id: role.actions for role in mcp_roles}

        identity_sources = self.repo.user.get_identity_sources_in_settings([row[0].id for row in users])

        api_list = []
        for user, profile, last_signed_in_at, latest_business_activity_at in users:
            api_user = user.api_response()
            if profile:
                api_user.update(profile.api_response())
            else:
                api_user.update(
                    {
                        "industry": "",
                        "purpose": "",
                        "affiliation": None,
                        "position": None,
                    }
                )
            api_user["identity_sources"] = identity_sources.get(user.id, [])
            api_user["created_at"] = user.created_at
            api_user["activated_at"] = user.activated_at
            api_user["account_status"] = "active" if user.activated_at else "inactive"
            api_user["last_signed_in_at"] = (
                last_signed_in_at.replace(tzinfo=timezone.utc)
                if last_signed_in_at and last_signed_in_at.tzinfo is None
                else last_signed_in_at
            )
            api_user["latest_business_activity_at"] = (
                latest_business_activity_at.replace(tzinfo=timezone.utc)
                if latest_business_activity_at and latest_business_activity_at.tzinfo is None
                else latest_business_activity_at
            )
            api_user["is_admin"] = user.is_admin
            api_user["setting_role_actions"] = setting_role_actions_dicts.get(user.id, [])
            api_user["api_key_role_actions"] = api_key_role_actions_dicts.get(user.id, [])
            api_user["mcp_role_actions"] = mcp_role_actions_dicts.get(user.id, [])
            api_list.append(api_user)

        return api_list

    def count_by_status_in_settings(self, **filters) -> dict[str, int]:
        return self.repo.user.count_by_status_in_settings(**filters)

    def count_in_settings(
        self,
        query: str = "",
        account_status: str = "all",
        *,
        column_filters: dict[str, str] | None = None,
        identity_provider: str = "all",
        admin_role: str = "all",
        exclude_user_uid: str | None = None,
        last_signed_in_from: datetime | None = None,
        last_signed_in_before: datetime | None = None,
        last_business_activity_from: datetime | None = None,
        last_business_activity_before: datetime | None = None,
    ) -> int:
        return self.repo.user.count_in_settings(
            query=query,
            account_status=account_status,
            column_filters=column_filters,
            identity_provider=identity_provider,
            admin_role=admin_role,
            exclude_user_uid=exclude_user_uid,
            last_signed_in_from=last_signed_in_from,
            last_signed_in_before=last_signed_in_before,
            last_business_activity_from=last_business_activity_from,
            last_business_activity_before=last_business_activity_before,
        )

    def count_not_deleted(self) -> int:
        return self.repo.user.count_not_deleted()

    def get_by_email(self, email: str | None):
        if not email:
            return None, None
        return self.repo.user.get_by_email(email)

    def get_by_token(self, token: str | None, key: str | None) -> tuple[User, UserEmail | None] | tuple[None, None]:
        if not token or not key:
            return None, None
        email = Encryptor.decrypt(token, key)
        return self.repo.user.get_by_email(email)

    def get_api_profile(self, user: TUserParam) -> dict[str, Any]:
        profile = self.repo.user_profile.get_by_user(user)
        if not profile:
            return {}
        return profile.api_response()

    def create(self, form: dict, avatar: FileModel | None = None) -> tuple[User, UserProfile]:
        user = User(**form)
        user.avatar = avatar

        self.repo.user.insert(user)

        user_profile = UserProfile(user_id=user.id, **form)
        self.repo.user_profile.insert(user_profile)

        return user, user_profile

    def create_subemail(self, user: TUserParam, email: str) -> UserEmail:
        user_id = InfraHelper.convert_id(user)
        user_email = UserEmail(user_id=user_id, email=email)
        self.repo.user_email.insert(user_email)

        return user_email

    def get_subemails(self, user: User) -> list[dict[str, Any]]:
        if not user.id:
            return []
        raw_subemails = self.repo.user_email.get_all_by_user(user.id)
        subemails = [subemail.api_response() for subemail in raw_subemails]
        return subemails

    def create_token_url(
        self,
        user: User,
        cache_key: str,
        token_query_name: UI_QUERY_NAMES,
        extra_token_data: dict | None = None,
    ) -> str:
        token = generate_random_string(32)
        token_expire_hours = 24
        token_data = json_dumps({"token": token, "id": user.id})
        encrypted_token = Encryptor.encrypt(token_data, Env.COMMON_SECRET_KEY)

        url_chunks = urlparse(Env.UI_REDIRECT_URL)
        token_url = url_chunks._replace(
            query=concat(
                url_chunks.query,
                "&" if url_chunks.query else "",
                token_query_name.value,
                "=",
                encrypted_token,
            )
        ).geturl()

        cache_token_Data = {"token": token, "id": user.id}
        if extra_token_data:
            cache_token_Data["extra"] = extra_token_data

        Cache.set(cache_key, cache_token_Data, 60 * 60 * token_expire_hours)

        return token_url

    def validate_token_from_url(
        self, token_type: str, token: str
    ) -> tuple[User, str, dict | None] | tuple[None, None, None]:
        try:
            token_info = json_loads(Encryptor.decrypt(token, Env.COMMON_SECRET_KEY))
            if not token_info or "token" not in token_info or "id" not in token_info:
                raise Exception()
        except Exception:
            return None, None, None

        user = InfraHelper.get_by(User, "id", token_info["id"])
        if not user:
            return None, None, None

        cache_key = self.create_cache_name(token_type, user.email)

        cached_token_info: dict | None = Cache.get(cache_key)
        if (
            not cached_token_info
            or cached_token_info["id"] != user.id
            or cached_token_info["token"] != token_info["token"]
        ):
            return None, None, None

        return user, cache_key, cached_token_info.get("extra")

    def activate(self, user: User) -> None:
        user.activated_at = SafeDateTime.now()
        self.repo.user.update(user)

        invitation_service: "ProjectInvitationService" = self._get_service_by_name("project_invitation")
        invitation_service.update_by_signed_up(user)

    def verify_subemail(self, subemail: UserEmail) -> None:
        subemail.verified_at = SafeDateTime.now()
        self.repo.user_email.update(subemail)

    def update(self, user: User, form: dict, from_setting: bool = False) -> bool:
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(User).where(User.id == user.id).with_for_update()).first()
            if current is None:
                return False
            if from_setting and "activated_at" in form and form["activated_at"] is None:
                self._assert_no_organization_ownership([current.id], db)
                if current.activated_at is not None:
                    current.auth_version += 1
            expected = form.get("expected_avatar_revision")
            if (form.get("avatar") is not None or form.get("delete_avatar")) and expected is not None:
                from .AvatarService import AvatarError

                if type(expected) is not int or expected != current.avatar_revision:
                    raise AvatarError("avatar_revision_conflict", 409)
            result = self._update_locked(current, form, from_setting, db)
            if from_setting and form.get("password"):
                self.change_password(current, form["password"])

            # Keep callers holding the authenticated model consistent after commit.
            def refresh_caller():
                for field in (
                    "firstname",
                    "lastname",
                    "avatar",
                    "previous_avatar",
                    "avatar_revision",
                    "is_admin",
                    "activated_at",
                    "auth_version",
                ):
                    setattr(user, field, getattr(current, field))

            db.after_commit(refresh_caller)
            return result

    def _update_locked(self, user: User, form: dict, from_setting: bool, db) -> bool:
        previous_avatar = user.avatar
        profile = self.repo.user_profile.get_by_user(user)
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                industry="",
                purpose="",
            )

        validators: TMutableValidatorMap = {
            "firstname": "default",
            "lastname": "default",
            "avatar": "default",
        }
        profile_validators: TMutableValidatorMap = {
            "affiliation": "default",
            "position": "default",
        }
        if from_setting:
            validators.update(
                {
                    "is_admin": "default",
                    "activated_at": "nullable",
                }
            )
            profile_validators.update(
                {
                    "industry": "default",
                    "purpose": "default",
                }
            )

        old_record = self.apply_mutates(user, form, validators)
        old_record.update(self.apply_mutates(profile, form, profile_validators))

        if "delete_avatar" in form and form["delete_avatar"]:
            old_record["avatar"] = convert_python_data(user.avatar)
            user.avatar = None

        if user.avatar != previous_avatar:
            user.previous_avatar = previous_avatar
            user.avatar_revision += 1

        if not old_record:
            if profile.is_new():
                self.repo.user_profile.insert(profile)
            return True

        if profile.is_new():
            self.repo.user.update(user)
            self.repo.user_profile.insert(profile)
        else:
            self.repo.user.update([user, profile])

        model: dict[str, Any] = {}
        for key in form:
            if key not in validators or key not in old_record:
                continue
            if key == "avatar":
                model[key] = user.avatar.path if user.avatar else None
            else:
                model[key] = convert_python_data(getattr(user, key))

        if user.avatar != previous_avatar:
            model["avatar"] = user.avatar.path if user.avatar else None
            model["avatar_revision"] = user.avatar_revision

        setting_model = {
            **model,
            **{key: convert_python_data(getattr(profile, key)) for key in profile_validators if key in old_record},
        }

        def publish():
            Auth.reset_user(user)
            UserPublisher.updated(user, model)
            if from_setting and setting_model:
                AppSettingPublisher.user_updated(user.get_uid(), setting_model)
            if from_setting and "activated_at" in form and not user.activated_at:
                UserPublisher.deactivated(user)

        db.after_commit(publish)

        return True

    def get_preferred_lang(self, user: User) -> str:
        preferred_lang = self.repo.user.get_preferred_lang(user)
        if preferred_lang:
            return preferred_lang
        instance_language = Env.INSTANCE_DEFAULT_LANGUAGE
        return instance_language if instance_language in LangEnum._value2member_map_ else LangEnum.enUS.value

    def update_preferred_lang(self, user: User, lang: str):
        if lang in LangEnum.__members__:
            lang = LangEnum[lang].value
        elif lang in LangEnum._value2member_map_:
            lang = lang
        else:
            return False

        self.repo.user.update_preferred_lang(user, lang)
        user.preferred_lang = lang

        return True

    def change_primary_email(self, user: User, subemail: UserEmail) -> bool:
        user_email = user.email
        user.email = subemail.email
        subemail.email = user_email

        self.repo.user.update([user, subemail])

        Auth.reset_user(user)

        model = {"email": user.email}
        UserPublisher.updated(user, model)

        return True

    def delete_email(self, subemail: UserEmail) -> bool:
        self.repo.user_email.delete(subemail)

        return True

    def change_password(self, user: User, password: str) -> None:
        user.set_password(password)
        self.repo.user.update(user)

    def get_setting_role(self, user: TUserParam) -> SettingRole | None:
        user_id = InfraHelper.convert_id(user)
        return self.repo.role.setting.get_one(user_id=user_id)

    def can_search_all_users(self, user: User) -> bool:
        if user.email in Env.FULL_ADMIN_ACCESS_EMAILS:
            return True

        if not user.is_admin:
            return False

        role = self.get_setting_role(user)
        return bool(role and role.is_granted(SettingRoleAction.UserRead))

    def grant_setting_roles(
        self,
        user: TUserParam,
        actions: SettingRoleAction | str | list[SettingRoleAction | str] | list[SettingRoleAction] | list[str],
    ) -> SettingRole:
        user_id = InfraHelper.convert_id(user)
        if not isinstance(actions, list):
            actions = [actions]
        action_strs = [action.value if isinstance(action, SettingRoleAction) else action for action in actions]

        # Handle Read permission dependencies for SettingRole
        action_strs = self._normalize_setting_role_actions(action_strs)

        role = self.repo.role.setting.grant(actions=action_strs, user_id=user_id)

        UserPublisher.setting_roles_updated(InfraHelper.convert_uid(user), role.actions)

        return role

    def _normalize_setting_role_actions(self, actions: list[str]) -> list[str]:
        """Ensure Read permission dependencies are properly handled for SettingRole"""
        if not actions:
            return actions

        result = set(actions)

        # Dynamically get actions for each category
        for category in SettingRoleCategory:
            category_value = category.value
            read_action = f"{category_value}_read"

            # Get all actions for this category by checking SettingRoleAction enum
            category_actions = [
                action.value for action in SettingRoleAction if action.value.startswith(f"{category_value}_")
            ]

            if not category_actions:
                continue

            dependent_actions = [action for action in category_actions if action != read_action]

            # If any Create/Update/Delete is added, add Read too
            if any(action in result for action in dependent_actions):
                result.add(read_action)

            # If Read is removed, remove all dependent actions
            if read_action not in result:
                for action in dependent_actions:
                    result.discard(action)

        return list(result)

    def grant_all_setting_roles(self, user: TUserParam) -> SettingRole:
        user_id = InfraHelper.convert_id(user)
        role = self.repo.role.setting.grant_all(user_id=user_id)

        UserPublisher.setting_roles_updated(InfraHelper.convert_uid(user), role.actions)

        return role

    def _assert_no_organization_ownership(self, user_ids: list[SnowflakeID], db: DbSession) -> None:
        owned = db.exec(
            SqlBuilder.select.table(Organization).where(Organization.owner_user_id.in_(user_ids)).limit(1)
        ).first()
        if owned is not None:
            raise UserOwnershipError("Transfer organization ownership before deactivating or deleting its owner.")

    def delete(self, user: User) -> None:
        with DbSession.atomic() as db:
            db.exec(SqlBuilder.select.table(User).where(User.id == user.id).with_for_update()).first()
            self._assert_no_organization_ownership([user.id], db)
            self.repo.user.delete(user)

            def publish():
                Auth.reset_user(user)
                UserPublisher.deleted(user)

            db.after_commit(publish)

    def delete_selected(self, users: Sequence[TUserParam]) -> None:
        if not isinstance(users, Sequence) or isinstance(users, str):
            users = [users]

        ids = [InfraHelper.convert_id(user) for user in users]
        uids = [InfraHelper.convert_uid(user) for user in users]
        with DbSession.atomic() as db:
            db.exec(SqlBuilder.select.table(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()).all()
            self._assert_no_organization_ownership(ids, db)
            self.repo.user.delete(users)

            def publish():
                AppSettingPublisher.selected_users_deleted(uids)
                for user_id in ids:
                    UserPublisher.deleted(user_id)

            db.after_commit(publish)

    def log_sign_in(
        self,
        user: User,
        is_success: bool = True,
        ip_address: str | None = None,
        error_code: SignInErrorCode | None = None,
    ) -> None:
        login_history = UserSignInHistory(
            user_id=user.id, is_success=is_success, ip_address=ip_address, error_code=error_code
        )
        self.repo.user_sign_in_history.insert(login_history)
