from typing import Sequence
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TProjectParam, TUserParam
from ....domain.models import ProjectAssignedUser, ProjectUserRelationship
from ....helpers import InfraHelper


class ProjectUserRelationshipRepository(BaseRepository[ProjectUserRelationship]):
    @staticmethod
    def model_cls():
        return ProjectUserRelationship

    @staticmethod
    def name() -> str:
        return "project_user_relationship"

    def ensure_project_relationships(self, project: TProjectParam, users: Sequence[TUserParam]) -> None:
        project_id = InfraHelper.convert_id(project)
        user_ids = sorted({InfraHelper.convert_id(user) for user in users})
        if len(user_ids) < 2:
            return

        requested_pairs = {
            (user_id, related_user_id)
            for user_id in user_ids
            for related_user_id in user_ids
            if user_id != related_user_id
        }

        with DbSession.use(readonly=False) as db:
            existing_pairs: set[tuple[SnowflakeID, SnowflakeID]] = set()
            for user_id, related_user_id in db.exec(
                SqlBuilder.select.columns(
                    ProjectUserRelationship.column("user_id"),
                    ProjectUserRelationship.column("related_user_id"),
                )
                .where(ProjectUserRelationship.column("project_id") == project_id)
                .where(ProjectUserRelationship.column("user_id").in_(user_ids))
                .where(ProjectUserRelationship.column("related_user_id").in_(user_ids))
            ).all():
                if user_id is None or related_user_id is None:
                    continue
                existing_pairs.add((user_id, related_user_id))
            missing_pairs = requested_pairs - existing_pairs
            if missing_pairs:
                db.insert_all(
                    [
                        ProjectUserRelationship(
                            project_id=project_id,
                            user_id=user_id,
                            related_user_id=related_user_id,
                        )
                        for user_id, related_user_id in missing_pairs
                    ]
                )

            db.exec(
                SqlBuilder.update.table(ProjectUserRelationship)
                .values(last_related_at=SafeDateTime.now())
                .where(ProjectUserRelationship.column("project_id") == project_id)
                .where(ProjectUserRelationship.column("user_id").in_([user_id for user_id, _ in requested_pairs]))
                .where(
                    ProjectUserRelationship.column("related_user_id").in_(
                        [related_user_id for _, related_user_id in requested_pairs]
                    )
                )
            )

    def backfill_from_current_project_members(self) -> None:
        with DbSession.use(readonly=True) as db:
            rows = db.exec(
                SqlBuilder.select.columns(
                    ProjectAssignedUser.column("project_id"),
                    ProjectAssignedUser.column("user_id"),
                )
            ).all()

        by_project: dict[SnowflakeID, set[SnowflakeID]] = {}
        for project_id, user_id in rows:
            if project_id is None or user_id is None:
                continue
            by_project.setdefault(project_id, set()).add(user_id)

        for project_id, user_ids in by_project.items():
            self.ensure_project_relationships(project_id, list(user_ids))
