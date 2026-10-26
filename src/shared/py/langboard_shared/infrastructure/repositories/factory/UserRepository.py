from collections.abc import Sequence
from datetime import datetime
from sqlalchemy import and_, func, or_, select
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TUserParam
from ....domain.models import (
    IdentityProvider,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectUserRelationship,
    ProjectWikiActivity,
    User,
    UserEmail,
    UserIdentityLink,
    UserProfile,
    UserSignInHistory,
)
from ....domain.models.ProjectActivity import ProjectActivityType
from ....domain.models.ProjectWikiActivity import ProjectWikiActivityType
from ....helpers import InfraHelper


class UserRepository(BaseRepository[User]):
    # Explicit mutation allowlists shared by account aggregates and detail feeds.
    BUSINESS_PROJECT_ACTIONS = (
        ProjectActivityType.ProjectCreated,
        ProjectActivityType.ProjectUpdated,
        ProjectActivityType.ProjectDeleted,
        ProjectActivityType.ProjectColumnCreated,
        ProjectActivityType.ProjectColumnNameChanged,
        ProjectActivityType.ProjectColumnDeleted,
        ProjectActivityType.ProjectLabelCreated,
        ProjectActivityType.ProjectLabelUpdated,
        ProjectActivityType.ProjectLabelDeleted,
        ProjectActivityType.CardCreated,
        ProjectActivityType.CardLinked,
        ProjectActivityType.CardCloned,
        ProjectActivityType.CardUpdated,
        ProjectActivityType.CardMoved,
        ProjectActivityType.CardAssignedUsersUpdated,
        ProjectActivityType.CardLabelsUpdated,
        ProjectActivityType.CardDeleted,
        ProjectActivityType.CardAttachmentUploaded,
        ProjectActivityType.CardAttachmentNameChanged,
        ProjectActivityType.CardAttachmentDeleted,
        ProjectActivityType.CardCommentAdded,
        ProjectActivityType.CardCommentUpdated,
        ProjectActivityType.CardCommentDeleted,
        ProjectActivityType.CardCommentReacted,
        ProjectActivityType.CardCommentUnreacted,
        ProjectActivityType.CardRelationshipsUpdated,
        ProjectActivityType.CardChecklistCreated,
        ProjectActivityType.CardChecklistTitleChanged,
        ProjectActivityType.CardChecklistChecked,
        ProjectActivityType.CardChecklistUnchecked,
        ProjectActivityType.CardChecklistDeleted,
        ProjectActivityType.CardCheckitemCreated,
        ProjectActivityType.CardCheckitemTitleChanged,
        ProjectActivityType.CardCheckitemTimerStarted,
        ProjectActivityType.CardCheckitemTimerPaused,
        ProjectActivityType.CardCheckitemTimerStopped,
        ProjectActivityType.CardCheckitemChecked,
        ProjectActivityType.CardCheckitemUnchecked,
        ProjectActivityType.CardCheckitemCardified,
        ProjectActivityType.CardCheckitemDeleted,
    )
    BUSINESS_WIKI_ACTIONS = (
        ProjectWikiActivityType.WikiCreated,
        ProjectWikiActivityType.WikiUpdated,
        ProjectWikiActivityType.WikiDeleted,
    )

    @staticmethod
    def model_cls():
        return User

    @staticmethod
    def name() -> str:
        return "user"

    def get_preferred_lang(self, user: User) -> str:
        # Account bootstrap must observe a preference write before replica replay.
        query = select(User.column("preferred_lang")).where(User.column("id") == user.id)
        with DbSession.use(readonly=False) as db:
            row = db.exec(query).first()
        return row[0] if row else user.preferred_lang

    def update_preferred_lang(self, user: User, lang: str) -> None:
        # The authentication model may still contain the requested replica value.
        with DbSession.use(readonly=False) as db:
            db.exec(
                SqlBuilder.update.table(User)
                .where(User.column("id") == user.id)
                .values({User.column("preferred_lang"): lang, User.column("updated_at"): SafeDateTime.now()})
            )

    @staticmethod
    def _latest_business_activity():
        """Use persisted mutation actors, excluding identity and access configuration.

        Explicit allowlists keep future read-only/security events out by default.
        UserActivity is only a mirror and is absent for some atomic card mutations.
        """
        events = (
            select(ProjectActivity.column("user_id"), ProjectActivity.column("created_at"))
            .where(
                ProjectActivity.column("activity_type").in_(UserRepository.BUSINESS_PROJECT_ACTIONS),
                ProjectActivity.column("bot_id").is_(None),
            )
            .union_all(
                select(ProjectWikiActivity.column("user_id"), ProjectWikiActivity.column("created_at")).where(
                    ProjectWikiActivity.column("activity_type").in_(UserRepository.BUSINESS_WIKI_ACTIONS),
                    ProjectWikiActivity.column("bot_id").is_(None),
                )
            )
            .subquery()
        )
        return (
            select(func.max(events.c.created_at))
            .where(events.c.user_id == User.column("id"))
            .correlate(User)
            .scalar_subquery()
        )

    @staticmethod
    def _settings_filters(
        query: str = "",
        account_status: str = "all",
        *,
        column_filters: dict[str, str] | None = None,
        identity_provider: str = "all",
        exclude_user_uid: str | None = None,
        last_signed_in_from: datetime | None = None,
        last_signed_in_before: datetime | None = None,
        last_business_activity_from: datetime | None = None,
        last_business_activity_before: datetime | None = None,
        user_uid: str | None = None,
    ):
        filters = [User.column("deleted_at").is_(None)]
        if user_uid is not None:
            filters.append(User.column("id") == SnowflakeID.from_short_code(user_uid))
        if account_status == "active":
            filters.append(User.column("activated_at").is_not(None))
        elif account_status == "inactive":
            filters.append(User.column("activated_at").is_(None))
        elif account_status != "all":
            raise ValueError("Invalid account status")
        if identity_provider != "all":
            provider = IdentityProvider(identity_provider)
            filters.append(
                select(1)
                .where(
                    UserIdentityLink.column("user_id") == User.column("id"),
                    UserIdentityLink.column("provider") == provider,
                )
                .correlate(User)
                .exists()
            )
        if exclude_user_uid:
            filters.append(User.column("id") != SnowflakeID.from_short_code(exclude_user_uid))
        for field, value in (column_filters or {}).items():
            if field not in ("email", "firstname", "lastname"):
                raise ValueError("Invalid user filter")
            if value.strip():
                filters.append(func.lower(User.column(field)).contains(value.strip().lower(), autoescape=True))
        if last_signed_in_from is not None or last_signed_in_before is not None:
            # Compare the latest successful event, never any matching historical event.
            latest = (
                select(func.max(UserSignInHistory.column("signed_in_at")))
                .where(
                    UserSignInHistory.column("user_id") == User.column("id"),
                    UserSignInHistory.column("is_success").is_(True),
                )
                .correlate(User)
                .scalar_subquery()
            )
            if last_signed_in_from is not None:
                filters.append(latest >= last_signed_in_from)
            if last_signed_in_before is not None:
                filters.append(latest < last_signed_in_before)
        if last_business_activity_from is not None or last_business_activity_before is not None:
            latest = UserRepository._latest_business_activity()
            if last_business_activity_from is not None:
                filters.append(latest >= last_business_activity_from)
            if last_business_activity_before is not None:
                filters.append(latest < last_business_activity_before)
        query = query.strip()
        if query:
            matches = [
                func.lower(User.column(field)).contains(query.lower(), autoescape=True)
                for field in ("email", "firstname", "lastname", "username")
            ]
            try:
                user_id = SnowflakeID.from_short_code(query)
            except ValueError:
                user_id = 0
            if user_id:
                matches.append(User.column("id") == user_id)
            filters.append(or_(*matches))
        return filters

    def count_in_settings(
        self,
        query: str = "",
        account_status: str = "all",
        *,
        column_filters: dict[str, str] | None = None,
        identity_provider: str = "all",
        exclude_user_uid: str | None = None,
        last_signed_in_from: datetime | None = None,
        last_signed_in_before: datetime | None = None,
        last_business_activity_from: datetime | None = None,
        last_business_activity_before: datetime | None = None,
    ) -> int:
        with DbSession.use(readonly=True) as db:
            return (
                db.exec(
                    SqlBuilder.select.count(User, User.column("id")).where(
                        *self._settings_filters(
                            query,
                            account_status,
                            column_filters=column_filters,
                            identity_provider=identity_provider,
                            exclude_user_uid=exclude_user_uid,
                            last_signed_in_from=last_signed_in_from,
                            last_signed_in_before=last_signed_in_before,
                            last_business_activity_from=last_business_activity_from,
                            last_business_activity_before=last_business_activity_before,
                        )
                    )
                ).first()
                or 0
            )

    def get_all_with_profile_in_settings(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        query: str = "",
        account_status: str = "all",
        column_filters: dict[str, str] | None = None,
        identity_provider: str = "all",
        exclude_user_uid: str | None = None,
        last_signed_in_from: datetime | None = None,
        last_signed_in_before: datetime | None = None,
        last_business_activity_from: datetime | None = None,
        last_business_activity_before: datetime | None = None,
        sort_by: str = "created_at",
        sort_direction: str = "desc",
        user_uid: str | None = None,
        _with_business_activity: bool = False,
    ):
        filters = self._settings_filters(
            query,
            account_status,
            column_filters=column_filters,
            identity_provider=identity_provider,
            exclude_user_uid=exclude_user_uid,
            last_signed_in_from=last_signed_in_from,
            last_signed_in_before=last_signed_in_before,
            last_business_activity_from=last_business_activity_from,
            last_business_activity_before=last_business_activity_before,
            user_uid=user_uid,
        )
        if sort_by not in ("email", "firstname", "lastname", "created_at") or sort_direction not in ("asc", "desc"):
            raise ValueError("Invalid user sort")
        sort_column = User.column(sort_by)
        ordering = (sort_column.asc() if sort_direction == "asc" else sort_column.desc(), User.column("id").desc())
        page_ids = select(User.column("id")).where(*filters).order_by(*ordering)
        if limit is not None:
            page_ids = page_ids.offset(offset).limit(limit)

        # Aggregate once for the list; failed attempts never advance last login.
        last_sign_ins = (
            select(
                UserSignInHistory.column("user_id"),
                func.max(UserSignInHistory.column("signed_in_at")).label("last_signed_in_at"),
            )
            .where(
                UserSignInHistory.column("is_success").is_(True),
                UserSignInHistory.column("user_id").in_(page_ids),
            )
            .group_by(UserSignInHistory.column("user_id"))
            .subquery()
        )
        columns = [User, UserProfile, last_sign_ins.c.last_signed_in_at]
        if _with_business_activity:
            columns.append(self._latest_business_activity().label("latest_business_activity_at"))
        query = (
            SqlBuilder.select.tables(*columns)
            .outerjoin(UserProfile, User.column("id") == UserProfile.column("user_id"))
            .outerjoin(last_sign_ins, User.column("id") == last_sign_ins.c.user_id)
            .where(User.column("id").in_(page_ids))
            .order_by(*ordering)
        )

        records = []
        with DbSession.use(readonly=True) as db:
            result = db.exec(query)
            records = result.all()
        return records

    def get_all_with_profile_and_activity_in_settings(self, **kwargs):
        """Add business activity without changing the existing three-column caller contract."""
        return self.get_all_with_profile_in_settings(**kwargs, _with_business_activity=True)

    def count_not_deleted(self) -> int:
        with DbSession.use(readonly=True) as db:
            return (
                db.exec(
                    SqlBuilder.select.count(User, User.column("id")).where(User.column("deleted_at") == None)  # noqa
                ).first()
                or 0
            )

    def get_not_deleted_page(self, offset: int, limit: int) -> list[User]:
        with DbSession.use(readonly=True) as db:
            return db.exec(
                SqlBuilder.select.table(User)
                .where(User.column("deleted_at") == None)  # noqa
                .order_by(User.column("created_at").asc(), User.column("id").asc())
                .offset(offset)
                .limit(limit)
            ).all()

    def get_by_email(self, email: str | None):
        if not email:
            return None, None
        # Sign-in and recovery cannot accept a replica's pre-revocation state.
        with DbSession.use(readonly=False) as db:
            user = db.exec(
                SqlBuilder.select.table(User)
                .where(User.column("email") == email, User.column("deleted_at").is_(None))
                .limit(1)
            ).first()
            if user:
                return user, None
            result = db.exec(
                SqlBuilder.select.tables(User, UserEmail)
                .join(
                    UserEmail,
                    (User.column("id") == UserEmail.column("user_id")) & (UserEmail.column("deleted_at") == None),  # noqa
                )
                .where(UserEmail.column("email") == email, User.column("deleted_at").is_(None))
                .limit(1)
            )
            return result.first() or (None, None)

    def search_project_member_candidates(
        self,
        user: TUserParam,
        query: str,
        can_search_all_users: bool,
        organization_identities: Sequence[tuple[IdentityProvider, str]],
        limit: int = 20,
    ) -> list[User]:
        user_id = InfraHelper.convert_id(user)
        query = query.strip().lower()
        if len(query) < 2:
            return []

        search = f"%{query}%"
        search_clause = or_(
            func.lower(User.column("email")).like(search),
            func.lower(User.column("firstname")).like(search),
            func.lower(User.column("lastname")).like(search),
            func.lower(User.column("username")).like(search),
        )

        sql = (
            SqlBuilder.select.table(User)
            .where(User.column("id") != user_id)
            .where(User.column("deleted_at") == None)  # noqa: E711
            .where(User.column("activated_at") != None)  # noqa: E711
            .where(self._has_organization_identity(organization_identities))
            .where(search_clause)
            .order_by(User.column("firstname").asc(), User.column("lastname").asc(), User.column("id").asc())
            .limit(limit)
        )

        if not can_search_all_users:
            related_project_ids = select(ProjectAssignedUser.column("project_id")).where(
                ProjectAssignedUser.column("user_id") == user_id
            )
            current_related_user_exists = (
                select(ProjectAssignedUser.column("id"))
                .where(ProjectAssignedUser.column("user_id") == User.column("id"))
                .where(ProjectAssignedUser.column("project_id").in_(related_project_ids))
                .exists()
            )
            historical_related_user_exists = (
                select(ProjectUserRelationship.column("id"))
                .where(ProjectUserRelationship.column("user_id") == user_id)
                .where(ProjectUserRelationship.column("related_user_id") == User.column("id"))
                .exists()
            )
            sql = sql.where(current_related_user_exists | historical_related_user_exists)

        with DbSession.use(readonly=True) as db:
            result = db.exec(sql)
            return result.all()

    def get_direct_project_member_candidates(
        self,
        user: TUserParam,
        candidate_users: Sequence[TUserParam],
        can_search_all_users: bool,
        organization_identities: Sequence[tuple[IdentityProvider, str]],
    ) -> list[User]:
        """Resolve only active users inside the caller's candidate relationship scope."""

        user_id = InfraHelper.convert_id(user)
        candidate_ids = list(dict.fromkeys(InfraHelper.convert_id(candidate) for candidate in candidate_users))
        if not candidate_ids:
            return []

        sql = (
            SqlBuilder.select.table(User)
            .where(User.column("id").in_(candidate_ids))
            .where(User.column("id") != user_id)
            .where(User.column("deleted_at") == None)  # noqa: E711
            .where(User.column("activated_at") != None)  # noqa: E711
            .where(self._has_organization_identity(organization_identities))
            .order_by(User.column("id").asc())
        )

        if not can_search_all_users:
            related_project_ids = select(ProjectAssignedUser.column("project_id")).where(
                ProjectAssignedUser.column("user_id") == user_id
            )
            current_related_user_exists = (
                select(ProjectAssignedUser.column("id"))
                .where(ProjectAssignedUser.column("user_id") == User.column("id"))
                .where(ProjectAssignedUser.column("project_id").in_(related_project_ids))
                .exists()
            )
            historical_related_user_exists = (
                select(ProjectUserRelationship.column("id"))
                .where(ProjectUserRelationship.column("user_id") == user_id)
                .where(ProjectUserRelationship.column("related_user_id") == User.column("id"))
                .exists()
            )
            sql = sql.where(current_related_user_exists | historical_related_user_exists)

        with DbSession.use(readonly=True) as db:
            return db.exec(sql).all()

    @staticmethod
    def _has_organization_identity(organization_identities: Sequence[tuple[IdentityProvider, str]]):
        clauses = [
            and_(
                UserIdentityLink.column("provider") == provider,
                UserIdentityLink.column("issuer") == issuer,
            )
            for provider, issuer in organization_identities
        ]
        return (
            select(UserIdentityLink.column("id"))
            .where(UserIdentityLink.column("user_id") == User.column("id"))
            .where(or_(*clauses))
            .exists()
        )
