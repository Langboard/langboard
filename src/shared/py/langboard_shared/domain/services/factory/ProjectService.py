from typing import TYPE_CHECKING, Any, Literal, Sequence
from ....ai import BotScheduleHelper, BotScopeHelper
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.domain.BaseDomainService import TMutableValidatorMap
from ....core.schema import TimeBasedPagination
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TInternalBotParam, TProjectParam, TUserOrBot, TUserParam
from ....core.utils.Converter import convert_python_data
from ....Env import Env
from ....helpers import InfraHelper
from ....publishers import ProjectPublisher
from ....tasks.activities import ProjectActivityTask
from ....tasks.bots import ProjectBotTask
from ...models import (
    IdentityProvider,
    InternalBot,
    Project,
    ProjectAssignedInternalBot,
    ProjectAssignedUser,
    ProjectBotSchedule,
    ProjectBotScope,
    ProjectRole,
    User,
)
from ...models.bases import ALL_GRANTED
from ...models.Checkitem import CheckitemStatus
from ...models.InternalBot import InternalBotType
from ...models.ProjectRole import ProjectRoleAction
from ..CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from .CheckitemService import CheckitemService
from .DoclingMetadataService import DoclingMetadataService
from .ProjectLabelService import ProjectLabelService
from .ScimProvisioningService import ScimProvisioningService


if TYPE_CHECKING:
    from .ProjectInvitationService import ProjectInvitationService


class ProjectService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "project"

    def get_by_id_like(self, project: TProjectParam | None) -> Project | None:
        project = InfraHelper.get_by_id_like(Project, project)
        return project

    def get_api_list(
        self, user: User, limit: int | None = None, *, channel: CollaborationChannel = CollaborationChannel.Api,
        contexts: dict[int, CardVisibilityContext] | None = None,
    ) -> tuple[list[dict[str, Any]], list[SnowflakeID]]:
        if contexts is None:
            contexts = self._get_service_by_name("card").resolve_work_visibility_contexts(user, channel)
        raw_projects = self.repo.project.get_all_by_user(user, limit=limit, contexts=contexts)
        board_unread_counts = self.repo.project.get_unread_card_counts(user, [project.id for project, *_ in raw_projects], contexts=contexts)
        return self.__convert_project_list(user, raw_projects, board_unread_counts)

    def get_api_assigned_user_list(
        self,
        project: TProjectParam | None,
        where_user_in: Sequence[TUserParam] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not project:
            return []

        raw_users = self.repo.project_assigned_user.get_all_by_project(
            project,
            where_users_in=where_user_in,
            limit=limit,
        )

        users = [user.api_response() for user, _ in raw_users]
        return users

    def count_assigned_users(self, project: TProjectParam) -> int:
        return self.repo.project_assigned_user.count_by_project(project)

    def search_member_candidates(
        self, user: User, project: TProjectParam | None, query: str
    ) -> list[dict[str, Any]] | None:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None

        user_service = self._get_service_by_name("user")
        organization_identities = self.__organization_identities()
        if not organization_identities:
            return []
        users = self.repo.user.search_project_member_candidates(
            user,
            query,
            can_search_all_users=user_service.can_search_all_users(user),
            organization_identities=organization_identities,
        )
        return [user.api_response() for user in users]

    @staticmethod
    def __organization_identities() -> list[tuple[IdentityProvider, str]]:
        """Configured federated issuers define who is safe to add without consent."""

        pairs = (
            (IdentityProvider.Oidc, Env.OIDC_ISSUER),
            (IdentityProvider.Scim, Env.SCIM_ISSUER),
        )
        return [(provider, issuer.strip().rstrip("/")) for provider, issuer in pairs if issuer.strip()]

    def get_user_role_actions_by_project(self, user: User, project: Project) -> list[str]:
        if user.is_admin:
            return [ALL_GRANTED]
        role = self.repo.role.project.get_one(user_id=user.id, project_id=project.id)
        return role.actions if role else []

    def get_all_roles(self, user: User) -> list[ProjectRole]:
        roles = self.repo.role.project.get_list(user_id=user.id)
        return roles

    def get_api_assigned_internal_bot_list_with_setting_map(
        self, project: TProjectParam | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return [], {}

        self.__ensure_default_internal_bots(project)
        raw_internal_bots = self.repo.project_assigned_internal_bot.get_all_by_project(project)

        internal_bots = []
        internal_bot_settings = {}
        for internal_bot, assigned_bot in raw_internal_bots:
            internal_bots.append(internal_bot.api_response())
            internal_bot_settings[internal_bot.bot_type.value] = assigned_bot.api_response()
        return internal_bots, internal_bot_settings

    def get_assigned_internal_bot_by_type(
        self, project: TProjectParam | None, bot_type: InternalBotType
    ) -> tuple[InternalBot, ProjectAssignedInternalBot] | None:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None
        assignment = self.repo.project_assigned_internal_bot.find_with_internal_bot_by_project_and_type(project, bot_type)
        if assignment:
            return assignment

        # Older projects may predate a newly configured default bot. Repair only
        # when that bot is requested, rather than on every board/MCP detail read.
        self.__ensure_default_internal_bots(project)
        return self.repo.project_assigned_internal_bot.find_with_internal_bot_by_project_and_type(project, bot_type)

    def __ensure_default_internal_bots(self, project: Project) -> None:
        assigned_internal_bots = self.repo.project_assigned_internal_bot.get_all_by_project(project)
        assigned_bot_types = {internal_bot.bot_type for internal_bot, _ in assigned_internal_bots}
        default_internal_bots = self.repo.internal_bot.get_default_list()

        for internal_bot in default_internal_bots:
            if internal_bot.bot_type in assigned_bot_types:
                continue

            assigned_internal_bot = ProjectAssignedInternalBot(project_id=project.id, internal_bot_id=internal_bot.id)
            self.repo.project_assigned_internal_bot.insert(assigned_internal_bot)

    def get_api_bot_scope_list(self, project: TProjectParam | None, limit: int | None = None) -> list[dict[str, Any]]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return []

        scopes = BotScopeHelper.get_list(ProjectBotScope, limit=limit, project_id=project.id)
        return [scope.api_response() for scope in scopes]

    def get_api_bot_schedule_list(
        self, project: TProjectParam | None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return []

        pagination = TimeBasedPagination(page=1, limit=limit) if limit is not None else None
        schedules = BotScheduleHelper.get_all_by_scope(
            ProjectBotSchedule,
            None,
            project,
            as_api=True,
            pagination=pagination,
        )

        return schedules

    def get_api_starred_project_list(
        self, user: User, limit: int | None = None, *, channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> list[dict[str, Any]]:
        if not user or user.is_new():
            return []

        contexts = self._get_service_by_name("card").resolve_work_visibility_contexts(user, channel)
        raw_projects = self.repo.project.get_all_starred(user, limit=limit, contexts=contexts)
        projects, _ = self.__convert_project_list(user, raw_projects)
        return projects

    def __convert_project_list(
        self,
        user: User,
        raw_projects: list[tuple[Project, ProjectAssignedUser, SafeDateTime | None, bool, SafeDateTime | None]],
        board_unread_counts: dict[int, int] | None = None,
    ) -> tuple[list[dict[str, Any]], list[SnowflakeID]]:
        projects = []
        roles_dict = {}
        raw_project_ids = [project.id for project, _, _, _, _ in raw_projects]

        if not user.is_admin and raw_project_ids:
            roles = self.repo.role.project.get_list(user_id=user.id, project_id=raw_project_ids)  # type: ignore
            for role in roles:
                if role.project_id in roles_dict:
                    continue
                roles_dict[role.project_id] = role.actions

        project_ids = []
        for project, assigned_user, last_activity_at, related_to_current_user, related_activity_at in raw_projects:
            if not user.is_admin and project.id not in roles_dict:
                continue

            project_ids.append(project.id)
            api_project = project.api_response()
            api_project["starred"] = assigned_user.starred
            api_project["last_viewed_at"] = assigned_user.last_viewed_at
            api_project["view_count"] = assigned_user.view_count
            api_project["last_activity_at"] = last_activity_at
            api_project["related_to_current_user"] = related_to_current_user
            api_project["related_activity_at"] = related_activity_at
            api_project["current_auth_role_actions"] = roles_dict[project.id] if not user.is_admin else [ALL_GRANTED]
            if board_unread_counts is not None:
                api_project["board_unread_card_count"] = board_unread_counts.get(project.id, 0)
                api_project["board_has_unread_change"] = api_project["board_unread_card_count"] > 0
            projects.append(api_project)

        return projects, project_ids

    def get_details(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        is_setting: bool,
        limit: int | None = None,
    ) -> tuple[Project, dict[str, Any]] | None:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None

        response = project.api_response()
        owner = InfraHelper.get_by_id_like(User, project.owner_id, with_deleted=True)
        if not owner:
            return None

        invitation_service: "ProjectInvitationService" = self._get_service_by_name("project_invitation")
        invited_members = invitation_service.get_api_invited_user_list_by_project(project, limit=limit)
        all_members = []
        seen_member_uids = set()
        for member in [owner.api_response(), *self.get_api_assigned_user_list(project, limit=limit), *invited_members]:
            member_uid = member.get("uid")
            if member_uid in seen_member_uids:
                continue
            seen_member_uids.add(member_uid)
            all_members.append(member)

        member_classification = self._get_service(ScimProvisioningService).classify_members(
            [InfraHelper.convert_id(member["uid"]) for member in all_members]
        )
        for member in all_members:
            member["membership_classification"] = member_classification.get(member["uid"], "unknown")
        response["all_members"] = all_members
        response["invited_member_uids"] = [invitation["uid"] for invitation in invited_members]
        if isinstance(user_or_bot, User):
            response["current_auth_role_actions"] = self.get_user_role_actions_by_project(user_or_bot, project)
        response["labels"] = self._get_service(ProjectLabelService).get_api_list_by_project(project, limit=limit)
        if is_setting:
            roles = self.repo.role.project.get_list(project_id=project.id)
            response["member_roles"] = {}
            for role in roles:
                if role.user_id and role.user_id != project.owner_id:
                    response["member_roles"][role.user_id.to_short_code()] = role.actions

        return project, response

    def is_assigned(self, user: User, project: TProjectParam | None) -> tuple[bool, ProjectAssignedUser | None]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return False, None

        assignee = self.repo.project_assigned_user.find_by_user_and_project(user, project)
        return bool(assignee), assignee

    def get_assigned_project_uids(self, user: User, project_uids: Sequence[str]) -> set[str]:
        """Resolve a bounded subscription batch in one current membership query."""
        if len(project_uids) > 200:
            raise ValueError("At most 200 projects may be checked")
        project_ids = []
        for uid in set(project_uids):
            try:
                project_ids.append(InfraHelper.convert_id(uid))
            except (TypeError, ValueError):
                continue
        if not project_ids:
            return set()
        with DbSession.use(readonly=False) as db:
            rows = db.exec(
                SqlBuilder.select.column(Project.id)
                .join(ProjectAssignedUser, ProjectAssignedUser.project_id == Project.id)
                .where(ProjectAssignedUser.user_id == user.id, Project.id.in_(project_ids))
            ).all()
        return {project_id.to_short_code() for project_id in rows}

    def are_users_related(self, user: User, target_user: User, project: TProjectParam | None = None) -> bool:
        return bool(self.repo.project.are_users_related(user, target_user, project))

    def toggle_star(self, user: User, project: TProjectParam) -> bool:
        record = self.repo.project_assigned_user.get_with_project_by_user_and_project(user, project)
        if not record:
            return False
        assigned_user, project = record

        self.repo.project_assigned_user.update_starred(user, project, not assigned_user.starred)

        return True

    def set_last_view(self, user: User, project: Project) -> None:
        self.repo.project_assigned_user.set_last_view(user, project)

    def create(self, user: User, title: str, description: str | None = None, project_type: str = "Other") -> Project:
        project = Project(
            owner_id=user.id,
            title=title,
            description=description,
            project_type=project_type,
        )
        self.repo.project.insert(project)

        self.repo.project_column.get_or_create_archive_if_not_exists(project)

        self.repo.project_label.init_defaults(project)

        assigned_user = ProjectAssignedUser(project_id=project.id, user_id=user.id)
        self.repo.project_assigned_user.insert(assigned_user)

        self.repo.role.project.grant_all(user_id=user.id, project_id=project.id)

        internal_bots = self.repo.internal_bot.get_default_list()

        for internal_bot in internal_bots:
            assigned_internal_bot = ProjectAssignedInternalBot(project_id=project.id, internal_bot_id=internal_bot.id)
            self.repo.project_assigned_internal_bot.insert(assigned_internal_bot)

        with DbSession.use(readonly=False) as db:
            db.after_commit(lambda: ProjectActivityTask.project_created(user, project))

        return project

    def update(
        self, user_or_bot: TUserOrBot, project: TProjectParam | None, form: dict
    ) -> dict[str, Any] | Literal[True] | None:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None

        validators: TMutableValidatorMap = {
            "title": "not_empty",
            "description": "default",
            "project_type": "not_empty",
            "archive_visible_days": "default",
            "ai_description": "default",
        }

        old_record = self.apply_mutates(project, form, validators)
        if not old_record:
            return True

        self.repo.project.update(project)

        model: dict[str, Any] = {}
        for key in validators:
            if key not in form or key not in old_record:
                continue
            model[key] = convert_python_data(getattr(project, key))

        ProjectPublisher.updated(project, model)
        ProjectActivityTask.project_updated(user_or_bot, old_record, project)
        ProjectBotTask.project_updated(user_or_bot, project)

        return model

    def update_assigned_users(
        self,
        user: User,
        project: TProjectParam | None,
        emails: list[str],
        direct_members: list[User] | None = None,
    ) -> bool:
        with DbSession.atomic():
            return self._update_assigned_users(user, project, emails, direct_members)

    def _update_assigned_users(
        self,
        user: User,
        project: TProjectParam | None,
        emails: list[str],
        direct_members: list[User] | None = None,
    ) -> bool:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return False

        eligible_direct_members = self.__eligible_direct_members(user, direct_members) if direct_members else []
        desired_emails = list(dict.fromkeys([*emails, *(member.email for member in eligible_direct_members)]))

        old_assigned_users = self.repo.project_assigned_user.get_all_by_project(project)
        self.repo.project_user_relationship.ensure_project_relationships(
            project, [assigned_user.id for assigned_user, _ in old_assigned_users]
        )

        invitation_service: "ProjectInvitationService" = self._get_service_by_name("project_invitation")
        invitation_related_data = invitation_service.get_invitation_related_data(project, desired_emails)

        self.repo.project_assigned_user.delete_all_by_project_and_users(
            project, list(invitation_related_data.user_ids_should_delete)
        )

        result = invitation_service.invite_emails(
            user,
            project,
            invitation_related_data,
            direct_user_ids={member.id for member in eligible_direct_members},
        )

        new_assigned_users = self.repo.project_assigned_user.get_all_by_project(project)
        self.repo.project_user_relationship.ensure_project_relationships(
            project, [assigned_user.id for assigned_user, _ in new_assigned_users]
        )
        old_assigned_user_ids = {assigned_user.id for assigned_user, _ in old_assigned_users}
        newly_assigned_users = [
            assigned_user for assigned_user, _ in new_assigned_users if assigned_user.id not in old_assigned_user_ids
        ]
        model = {
            "assigned_members": [user.api_response() for user, _ in new_assigned_users],
            "invited_members": invitation_service.get_api_invited_user_list_by_project(project),
        }

        if old_assigned_user_ids != {assigned_user.id for assigned_user, _ in new_assigned_users}:
            self._get_service_by_name("card").invalidate_source_project_mirrors(project)

        ProjectPublisher.assigned_users_updated(project, model)
        ProjectPublisher.assigned_to_users(project, newly_assigned_users)
        ProjectActivityTask.project_assigned_users_updated(
            user,
            project,
            [user.id for user, _ in old_assigned_users],
            [user.id for user, _ in new_assigned_users],
        )

        return result

    def invite_assigned_users(
        self, user: User, project: TProjectParam | None, emails: list[str]
    ) -> dict[str, int | str] | None:
        """Invite new project members without removing existing relationships."""

        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None

        invitation_service: "ProjectInvitationService" = self._get_service_by_name("project_invitation")
        invitation_data = invitation_service.get_additive_invitation_related_data(project, emails)
        if not invitation_data.emails_should_invite:
            return {"requested_count": len(emails), "changed_count": 0, "status": "unchanged"}

        old_assigned_users = self.repo.project_assigned_user.get_all_by_project(project)
        invitation_service.invite_emails(user, project, invitation_data)
        changed_count = invitation_data.applied_count
        if not changed_count:
            return {"requested_count": len(emails), "changed_count": 0, "status": "unchanged"}

        new_assigned_users = self.repo.project_assigned_user.get_all_by_project(project)
        self.repo.project_user_relationship.ensure_project_relationships(
            project, [assigned_user.id for assigned_user, _ in new_assigned_users]
        )
        old_assigned_user_ids = {assigned_user.id for assigned_user, _ in old_assigned_users}
        newly_assigned_users = [
            assigned_user for assigned_user, _ in new_assigned_users if assigned_user.id not in old_assigned_user_ids
        ]
        ProjectPublisher.assigned_users_updated(
            project,
            {
                "assigned_members": [assigned_user.api_response() for assigned_user, _ in new_assigned_users],
                "invited_members": invitation_service.get_api_invited_user_list_by_project(project),
            },
        )
        ProjectPublisher.assigned_to_users(project, newly_assigned_users)
        ProjectActivityTask.project_assigned_users_updated(
            user,
            project,
            [assigned_user.id for assigned_user, _ in old_assigned_users],
            [assigned_user.id for assigned_user, _ in new_assigned_users],
        )
        return {
            "requested_count": len(emails),
            "changed_count": changed_count,
            "status": "updated" if changed_count else "unchanged",
        }

    def add_existing_assigned_users(
        self, user_or_bot: TUserOrBot, project: TProjectParam | None, users: list[User]
    ) -> dict[str, int | str] | None:
        """Immediately add existing accounts without creating invitations or removing members."""

        if not isinstance(user_or_bot, User):
            raise ValueError("Only users can add existing project members")
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None

        unique_users = {user.id: user for user in users if user.id is not None}
        eligible_user_map = {
            user.id: user for user in self.__eligible_direct_members(user_or_bot, list(unique_users.values()))
        }

        old_assigned_users = self.repo.project_assigned_user.get_all_by_project(project)
        newly_assigned_users: list[User] = []
        for target_user in eligible_user_map.values():
            _, created = self.repo.project_assigned_user.ensure_assigned(project, target_user)
            if created:
                self.repo.role.project.grant_all(user_id=target_user.id, project_id=project.id)
                newly_assigned_users.append(target_user)

        if not newly_assigned_users:
            return {"requested_count": len(unique_users), "changed_count": 0, "status": "unchanged"}

        new_assigned_users = self.repo.project_assigned_user.get_all_by_project(project)
        self.repo.project_user_relationship.ensure_project_relationships(
            project, [assigned_user.id for assigned_user, _ in new_assigned_users]
        )
        ProjectPublisher.assigned_users_updated(
            project,
            {
                "assigned_members": [assigned_user.api_response() for assigned_user, _ in new_assigned_users],
                "invited_members": self._get_service_by_name("project_invitation").get_api_invited_user_list_by_project(
                    project
                ),
            },
        )
        ProjectPublisher.assigned_to_users(project, newly_assigned_users)
        ProjectActivityTask.project_assigned_users_updated(
            user_or_bot,
            project,
            [assigned_user.id for assigned_user, _ in old_assigned_users],
            [assigned_user.id for assigned_user, _ in new_assigned_users],
        )
        return {"requested_count": len(unique_users), "changed_count": len(newly_assigned_users), "status": "updated"}

    def __eligible_direct_members(self, actor: User, users: list[User]) -> list[User]:
        user_service = self._get_service_by_name("user")
        organization_identities = self.__organization_identities()
        if not organization_identities:
            raise ValueError("Federated organization identity is not configured")
        unique_users = {user.id: user for user in users if user.id is not None}
        eligible = self.repo.user.get_direct_project_member_candidates(
            actor,
            list(unique_users.values()),
            can_search_all_users=user_service.can_search_all_users(actor),
            organization_identities=organization_identities,
        )
        if {user.id for user in eligible} != unique_users.keys():
            raise ValueError("One or more selected people are not eligible for direct addition")
        return eligible

    def unassign_assignee(self, user: User, project: TProjectParam | None, target: TUserParam | None) -> bool:
        project = InfraHelper.get_by_id_like(Project, project)
        target_user = InfraHelper.get_by_id_like(User, target)
        if not target_user:
            return False

        if not project or not (self.is_assigned(target_user, project))[0] or project.owner_id == target_user.id:
            return False

        users = self.repo.project_assigned_user.get_all_by_project(project)
        invitations = self.repo.project_invitation.get_all_by_project_with_user(project)
        new_user_emails = [
            *[user.email for user, _ in users if user.id != target_user.id],
            *[invitation.email for invitation, user in invitations if not user or user.id != target_user.id],
        ]

        return self.update_assigned_users(user, project, new_user_emails)

    def update_user_roles(
        self,
        project: TProjectParam | None,
        target_user: TUserParam | None,
        roles: list[ProjectRoleAction],
    ):
        with DbSession.atomic():
            return self._update_user_roles(project, target_user, roles)

    def _update_user_roles(
        self,
        project: TProjectParam | None,
        target_user: TUserParam | None,
        roles: list[ProjectRoleAction],
    ):
        project = InfraHelper.get_by_id_like(Project, project)
        target_user = InfraHelper.get_by_id_like(User, target_user)
        if not project or not target_user or not (self.is_assigned(target_user, project))[0]:
            return False

        if project.owner_id == target_user.id:
            return True

        if ProjectRoleAction.Read not in roles:
            roles.append(ProjectRoleAction.Read)

        role_strs = [role.value for role in roles]
        if roles == list(ProjectRoleAction._member_map_.values()):
            role = self.repo.role.project.grant_all(user_id=target_user.id, project_id=project.id)
        elif not roles:
            role = self.repo.role.project.grant_default(user_id=target_user.id, project_id=project.id)
        else:
            role = self.repo.role.project.grant(actions=role_strs, user_id=target_user.id, project_id=project.id)

        self._get_service_by_name("card").invalidate_source_project_mirrors(project)
        ProjectPublisher.user_roles_updated(project, target_user, role.actions)

        return True

    def change_internal_bot(self, project: TProjectParam | None, internal_bot: TInternalBotParam | None):
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return False

        internal_bot = InfraHelper.get_by_id_like(InternalBot, internal_bot)
        if not internal_bot:
            return False

        if internal_bot.bot_type not in InternalBotType.executable_types():
            return False

        result = self.repo.project_assigned_internal_bot.find_with_internal_bot_by_project_and_type(
            project, internal_bot.bot_type
        )
        if not result:
            return False
        old_internal_bot, _ = result

        if old_internal_bot.id == internal_bot.id:
            return True

        self.repo.project_assigned_internal_bot.replace_by_project(project, old_internal_bot, internal_bot)

        ProjectPublisher.internal_bot_changed(project, internal_bot.id)

        return True

    def change_internal_bot_settings(
        self,
        project: TProjectParam | None,
        bot_type: InternalBotType,
        use_default_prompt: bool | None = None,
        prompt: str | None = None,
    ):
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return False

        result = self.repo.project_assigned_internal_bot.find_with_internal_bot_by_project_and_type(project, bot_type)
        if not result:
            return False
        _, assigned_internal_bot = result

        should_update = False
        if use_default_prompt is not None and assigned_internal_bot.use_default_prompt != use_default_prompt:
            assigned_internal_bot.use_default_prompt = use_default_prompt
            should_update = True

        if prompt is not None and assigned_internal_bot.prompt != prompt:
            assigned_internal_bot.prompt = prompt
            should_update = True

        if not should_update:
            return True

        self.repo.project_assigned_internal_bot.update(assigned_internal_bot)

        ProjectPublisher.internal_bot_settings_changed(project, bot_type, assigned_internal_bot)

        return True

    def delete(self, user: User, project: TProjectParam | None) -> bool:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return False

        started_checkitems = self.repo.checkitem.get_all_started_checkitem_by_project(project)

        checkitem_service = self._get_service(CheckitemService)
        current_time = SafeDateTime.now()
        for checkitem, card in started_checkitems:
            checkitem_service.change_status(
                user,
                project,
                card,
                checkitem,
                CheckitemStatus.Stopped,
                current_time,
                should_publish=False,
            )

        with DbSession.atomic():
            from .ProjectDocumentService import ProjectDocumentService
            from .WikiDocumentService import WikiDocumentService

            self._get_service(ProjectDocumentService).record_scope_embedding_cleanup(project)
            self._get_service(WikiDocumentService).record_scope_embedding_cleanup(project)
            self._get_service(DoclingMetadataService).record_scope_embedding_cleanup(project)
            self.repo.project.delete(project)
            self._get_service_by_name("card").invalidate_source_project_mirrors(project)

        ProjectPublisher.deleted(project)
        ProjectActivityTask.project_deleted(user, project)
        ProjectBotTask.project_deleted(user, project)

        return True

    def group_invitation(
        self, actor_id, project_uid, group_uid, *, snapshot=None, request_id=None, confirmed_count=None
    ):
        """Preview or atomically apply a canonical group invitation with current authority."""
        from hashlib import sha256
        from json import dumps
        from uuid import UUID
        from sqlalchemy import text
        from ....core.db.DbEngine import DbEngine
        from ...models import (
            GroupDirectoryAudit,
            Organization,
            ProjectInvitation,
            ScimGroup,
            ScimGroupMember,
            SettingRole,
            UserEmail,
        )
        from ...models.SettingRole import SettingRoleAction
        from .GroupDirectoryService import GroupDirectoryConflict, GroupDirectoryDenied, GroupDirectoryMissing

        directory = self._get_service_by_name("group_directory")
        project_id, group_id = directory._uid(project_uid), directory._uid(group_uid)
        applying = request_id is not None
        if applying:
            request_id = str(UUID(request_id))
            if not isinstance(snapshot, str) or len(snapshot) != 64 or type(confirmed_count) is not int:
                raise ValueError("Explicit snapshot and target count required")
        with DbSession.atomic() as db:
            dialect = DbEngine.get_main_engine().dialect.name
            # Preview needs one coherent read snapshot, without blocking writers.
            # Apply matches canonical directory writer order. Table locks also cover
            # absent roles/membership/invitations; native writers share no row mutex.
            # ponytail: narrow apply locks once every native writer shares a tenant mutex.
            if dialect == "sqlite":
                db.exec(text("BEGIN IMMEDIATE" if applying else "BEGIN"))
            elif dialect == "postgresql":
                if applying:
                    db.exec(text("SET LOCAL lock_timeout = '3s'"))
                    db.exec(text("SET LOCAL statement_timeout = '10s'"))
                    db.exec(
                        text(
                            'LOCK TABLE organization, "user", setting_role, scim_group, scim_group_member, project, project_role, project_assigned_user, project_invitation, user_email, group_directory_audit IN SHARE ROW EXCLUSIVE MODE'
                        )
                    )
                else:
                    db.exec(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            else:
                raise ValueError("Group invitations require SQLite or PostgreSQL")
            actor = directory._authorized(db, actor_id, SettingRoleAction.GroupRead)
            directory._tenant(db)
            organization = db.exec(SqlBuilder.select.table(Organization)).first()
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == project_id)).first()
            group = db.exec(SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == group_id)).first()
            if project is None or group is None:
                raise GroupDirectoryMissing()
            if project.organization_id != organization.id:
                raise GroupDirectoryDenied()
            roles = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.project_id == project.id)).all()
            actor_roles = [role for role in roles if role.user_id == actor.id]
            if not (
                actor.is_admin
                or project.owner_id == actor.id
                or any(r.is_granted(ProjectRoleAction.Update) for r in actor_roles)
            ):
                raise GroupDirectoryDenied()
            fingerprint = sha256(
                dumps([str(actor.id), project_uid, group_uid, request_id, snapshot, confirmed_count]).encode()
            ).hexdigest()
            if applying:
                receipt = db.exec(
                    SqlBuilder.select.table(GroupDirectoryAudit).where(
                        GroupDirectoryAudit.actor_uid == actor.get_uid(),
                        GroupDirectoryAudit.action == "board_invitation",
                        GroupDirectoryAudit.change_facts["request_id"].as_string() == request_id,
                    )
                ).first()
                if receipt:
                    if receipt.change_facts["fingerprint"] != fingerprint:
                        raise GroupDirectoryConflict()
                    return {**receipt.change_facts["result"], "replayed": True}
            assignments = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.project_id == project.id)
            ).all()
            invitations = db.exec(
                SqlBuilder.select.table(ProjectInvitation).where(ProjectInvitation.project_id == project.id)
            ).all()
            members = db.exec(
                SqlBuilder.select.table(ScimGroupMember)
                .where(ScimGroupMember.group_id == group.id)
                .order_by(ScimGroupMember.user_id)
            ).all()
            ids = {member.user_id for member in members}
            users = (
                db.exec(
                    SqlBuilder.select.table(User, with_deleted=True).where(User.id.in_(ids)).order_by(User.id)
                ).all()
                if ids
                else []
            )
            aliases = db.exec(SqlBuilder.select.table(UserEmail).where(UserEmail.user_id.in_(ids))).all() if ids else []
            assigned_ids = {a.user_id for a in assignments} | {project.owner_id}
            pending_emails = {i.email.casefold() for i in invitations}
            aliases_by_id = {}
            for alias in aliases:
                aliases_by_id.setdefault(alias.user_id, set()).add(alias.email.casefold())
            classified = {key: [] for key in ("new", "existing", "inactive", "pending")}
            targets = []
            users_by_id = {u.id: u for u in users}
            for member in members:
                target = users_by_id.get(member.user_id)
                if target is None or target.deleted_at or not target.activated_at:
                    kind = "inactive"
                elif target.id in assigned_ids:
                    kind = "existing"
                elif ({target.email.casefold()} | aliases_by_id.get(target.id, set())) & pending_emails:
                    kind = "pending"
                else:
                    kind = "new"
                    targets.append(target)
                classified[kind].append(
                    {
                        "uid": member.user_id.to_short_code(),
                        "name": target.get_fullname() if target and not target.deleted_at else "",
                    }
                )
            facts = [
                actor.model_dump(mode="json"),
                organization.model_dump(mode="json"),
                project.model_dump(mode="json"),
                group.model_dump(mode="json"),
                [m.model_dump(mode="json") for m in members],
                [u.model_dump(mode="json") for u in users],
                [a.model_dump(mode="json") for a in assignments],
                [i.model_dump(mode="json") for i in invitations],
                [r.model_dump(mode="json") for r in roles],
                [
                    r.model_dump(mode="json")
                    for r in db.exec(SqlBuilder.select.table(SettingRole).where(SettingRole.user_id == actor.id)).all()
                ],
                [a.model_dump(mode="json") for a in aliases],
                Env.SCIM_ISSUER,
                list(Env.FULL_ADMIN_ACCESS_EMAILS),
            ]
            current_snapshot = sha256(dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
            result = {
                "group_uid": group_uid,
                "group_name": group.display_name,
                "snapshot": current_snapshot,
                "target_count": len(targets),
                "member_count": len(members),
                "counts": {key: len(value) for key, value in classified.items()},
                "members": classified,
            }
            if not applying:
                return result
            if snapshot != current_snapshot or confirmed_count != len(targets):
                raise GroupDirectoryConflict()
            invitation_service = self._get_service_by_name("project_invitation")
            data = invitation_service.get_additive_invitation_related_data(
                project, [target.email for target in targets]
            )
            invitation_service.invite_emails(actor, project, data, defer_assignment_effects=True)
            result = {
                "group_uid": group_uid,
                "target_count": len(targets),
                "changed_count": len(data.emails_should_invite),
                "replayed": False,
            }
            db.insert(
                GroupDirectoryAudit(
                    actor_uid=actor.get_uid(),
                    group_uid=group_uid,
                    action="board_invitation",
                    revision=group.revision,
                    change_facts={
                        "request_id": request_id,
                        "fingerprint": fingerprint,
                        "project_uid": project_uid,
                        "target_count": len(targets),
                        "result": result,
                    },
                )
            )
            return result
