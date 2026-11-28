from typing import cast
from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import InstrumentedAttribute
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TProjectParam, TUserParam
from ....domain.models import Card, CardAssignedUser, Project, ProjectActivity, ProjectAssignedUser
from ....domain.services.CardVisibilityPolicy import (
    CardVisibilityContext,
    card_visibility_scope_by_project,
    project_activity_visibility_scope,
)
from ....helpers import InfraHelper


class ProjectRepository(BaseRepository[Project]):
    @staticmethod
    def model_cls():
        return Project

    @staticmethod
    def name() -> str:
        return "project"

    def get_by_id_like(self, project: TProjectParam | None) -> Project | None:
        return InfraHelper.get_by_id_like(Project, project)

    def get_max_card_change_seq(
        self, project_ids: list[SnowflakeID], *, contexts: dict[int, CardVisibilityContext] | None = None,
    ) -> dict[int, int]:
        """Newest visible card change cursor per project."""

        if not project_ids:
            return {}
        rows = []
        query = select(Card.project_id, func.max(Card.last_change_seq)).where(Card.project_id.in_(project_ids))
        if contexts is not None:
            query = query.where(Card.deleted_at.is_(None), card_visibility_scope_by_project(contexts))
        with DbSession.use(readonly=contexts is None) as db:
            rows = db.exec(query.group_by(Card.project_id)).all()
        return {int(row[0]): int(row[1] or 0) for row in rows}

    def get_unread_card_counts(
        self, user: TUserParam, project_ids: list[SnowflakeID], *, contexts: dict[int, CardVisibilityContext],
    ) -> dict[int, int]:
        """Count distinct cards using the existing dashboard dot's audience and board cursor."""
        if not project_ids:
            return {}
        query = (
            select(Card.project_id, func.count(func.distinct(Card.id)))
            .join(ProjectAssignedUser, ProjectAssignedUser.project_id == Card.project_id)
            .where(
                ProjectAssignedUser.user_id == InfraHelper.convert_id(user),
                Card.project_id.in_(project_ids),
                Card.deleted_at.is_(None),
                card_visibility_scope_by_project(contexts),
                Card.last_change_seq > func.coalesce(ProjectAssignedUser.board_seen_seq, 0),
            )
            .group_by(Card.project_id)
        )
        # Membership, visibility and board cursors must use the primary snapshot.
        with DbSession.use(readonly=False) as db:
            rows = db.exec(query).all()
        return {int(row[0]): int(row[1]) for row in rows}

    def get_all_by_organization(self, organization_id: SnowflakeID, limit: int | None = None) -> list[Project]:
        """List projects mapped to one organization, newest first."""

        query = (
            SqlBuilder.select.table(Project)
            .where(Project.column("organization_id") == organization_id)
            .order_by(Project.column("updated_at").desc(), Project.column("id").desc())
        )
        if limit is not None:
            query = query.limit(limit)

        with DbSession.use(readonly=True) as db:
            return db.exec(query).all()

    def get_all_by_user(
        self, user: TUserParam, limit: int | None = None, *, contexts: dict[int, CardVisibilityContext] | None = None
    ) -> list[tuple[Project, ProjectAssignedUser, SafeDateTime | None, bool, SafeDateTime | None]]:
        user_id = InfraHelper.convert_id(user)
        last_activity_at = self._last_activity_at(contexts)
        related_to_current_user = self._related_to_user(user_id, contexts)
        related_activity = self._related_activity_by_project(user_id, contexts)
        query = (
            SqlBuilder.select.tables(Project, ProjectAssignedUser)
            .add_columns(last_activity_at, related_to_current_user, related_activity.c.related_activity_at)
            .join(
                ProjectAssignedUser,
                Project.column("id") == ProjectAssignedUser.column("project_id"),
            )
            .outerjoin(related_activity, related_activity.c.project_id == Project.column("id"))
            .where(ProjectAssignedUser.column("user_id") == user_id)
            .order_by(
                ProjectAssignedUser.column("starred").desc(),
                func.coalesce(last_activity_at, Project.column("created_at")).desc(),
                Project.column("id").desc(),
            )
        )
        if limit is not None:
            query = query.limit(limit)

        projects = []
        with DbSession.use(readonly=contexts is None) as db:
            result = db.exec(query)
            projects = result.all()
        return projects

    def get_all_starred(
        self, user: TUserParam, limit: int | None = None, *, contexts: dict[int, CardVisibilityContext] | None = None
    ) -> list[tuple[Project, ProjectAssignedUser, SafeDateTime | None, bool, SafeDateTime | None]]:
        user_id = InfraHelper.convert_id(user)
        last_activity_at = self._last_activity_at(contexts)
        related_to_current_user = self._related_to_user(user_id, contexts)
        related_activity = self._related_activity_by_project(user_id, contexts)
        query = (
            SqlBuilder.select.tables(Project, ProjectAssignedUser)
            .add_columns(last_activity_at, related_to_current_user, related_activity.c.related_activity_at)
            .join(
                ProjectAssignedUser,
                ProjectAssignedUser.column("project_id") == Project.column("id"),
            )
            .outerjoin(related_activity, related_activity.c.project_id == Project.column("id"))
            .where(ProjectAssignedUser.column("user_id") == user_id)
            .where(ProjectAssignedUser.column("starred") == True)  # noqa: E712
            .order_by(
                func.coalesce(last_activity_at, Project.column("created_at")).desc(),
                Project.column("id").desc(),
            )
        )
        if limit is not None:
            query = query.limit(limit)

        projects = []
        with DbSession.use(readonly=contexts is None) as db:
            result = db.exec(query)
            projects = result.all()
        return projects

    @staticmethod
    def _last_activity_at(contexts: dict[int, CardVisibilityContext] | None = None):
        query = SqlBuilder.select.column(func.max(ProjectActivity.column("created_at"))).where(
            ProjectActivity.column("project_id") == Project.column("id")
        )
        if contexts is not None:
            query = query.where(project_activity_visibility_scope(contexts))
        return query.correlate(Project).scalar_subquery().label("last_activity_at")

    @staticmethod
    def _related_to_user(user_id: SnowflakeID, contexts: dict[int, CardVisibilityContext] | None = None):
        query = (
            exists()
            .where(Card.column("project_id") == Project.column("id"))
            .where(Card.column("archived_at").is_(None))
            .where(Card.column("deleted_at").is_(None))
            .where(CardAssignedUser.column("card_id") == Card.column("id"))
            .where(CardAssignedUser.column("user_id") == user_id)
        )
        if contexts is not None:
            query = query.where(card_visibility_scope_by_project(contexts))
        return query.correlate(Project).label("related_to_current_user")

    @staticmethod
    def _related_activity_by_project(user_id: SnowflakeID, contexts: dict[int, CardVisibilityContext] | None = None):
        # Read the indexed latest activity per assigned card instead of joining
        # every historical event before grouping the user's related projects.
        last_card_activity = (
            SqlBuilder.select.column(func.max(ProjectActivity.column("created_at")))
            .where(ProjectActivity.column("card_id") == Card.column("id"))
            .correlate(Card)
            .scalar_subquery()
        )
        query = (
            SqlBuilder.select.columns(
                Card.column("project_id").label("project_id"),
                func.max(last_card_activity).label("related_activity_at"),
            )
            .join(CardAssignedUser, CardAssignedUser.column("card_id") == Card.column("id"))
            .where(Card.column("archived_at").is_(None))
            .where(Card.column("deleted_at").is_(None))
            .where(CardAssignedUser.column("user_id") == user_id)
        )
        if contexts is not None:
            query = query.where(card_visibility_scope_by_project(contexts))
        return query.group_by(Card.column("project_id")).subquery()

    def are_users_related(
        self, user: TUserParam, target_user: TUserParam, project: TProjectParam | None = None
    ) -> bool:
        user_id = InfraHelper.convert_id(user)
        target_user_id = InfraHelper.convert_id(target_user)

        user_a = aliased(ProjectAssignedUser)
        user_b = aliased(ProjectAssignedUser)

        query = (
            SqlBuilder.select.column(user_a.id)  # type: ignore
            .join(
                user_b,
                cast(InstrumentedAttribute, user_a.project_id) == user_b.project_id,
            )
            .where((user_a.user_id == user_id) & (user_b.user_id == target_user_id))
            .limit(1)
        )

        if project:
            project = InfraHelper.convert_id(project)
            query = query.where((user_a.project_id == project) & (user_b.project_id == project))

        record = None
        with DbSession.use(readonly=True) as db:
            result = db.exec(query)
            record = result.first()
        return bool(record)
