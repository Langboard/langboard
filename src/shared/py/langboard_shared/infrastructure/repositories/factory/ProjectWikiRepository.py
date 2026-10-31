from sqlalchemy import update
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseOrderRepository
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TProjectParam, TWikiParam
from ....domain.models import Project, ProjectWiki
from ....helpers import InfraHelper


class ProjectWikiRepository(BaseOrderRepository[ProjectWiki, Project]):
    @staticmethod
    def parent_model_cls():
        return Project

    @staticmethod
    def model_cls():
        return ProjectWiki

    @staticmethod
    def name() -> str:
        return "project_wiki"

    def get_by_id_like(self, wiki: TWikiParam | None) -> ProjectWiki | None:
        if not wiki:
            return None
        with DbSession.use(readonly=False) as db:
            return db.exec(
                SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == InfraHelper.convert_id(wiki))
            ).first()

    def update_content_if_current(
        self,
        wiki: ProjectWiki,
        expected_content: str,
        *,
        expected_generation: int,
        advance_generation: bool = False,
        title: str | None = None,
    ) -> bool:
        """Serialize saves/conversion on the live row; never infer a caller's lineage."""
        with DbSession.use(readonly=False) as db:
            current = db.exec(
                SqlBuilder.select.columns(ProjectWiki.column("content"), ProjectWiki.column("editor_generation"))
                .where((ProjectWiki.column("id") == wiki.id) & (ProjectWiki.column("project_id") == wiki.project_id))
                .with_for_update(),
                execution_options={"autoflush": False},
            ).first()
            if current is None or current[0].content != expected_content or current[1] != expected_generation:
                return False
            updated_at = SafeDateTime.now()
            generation = expected_generation + int(advance_generation)
            values = {"content": wiki.content, "editor_generation": generation, "updated_at": updated_at}
            if title is not None:
                values["title"] = title
            changed = db.exec(
                update(ProjectWiki.__table__)
                .where(
                    ProjectWiki.column("id") == wiki.id, ProjectWiki.column("editor_generation") == expected_generation
                )
                .values(**values),
                execution_options={"autoflush": False},
            )
            if changed != 1:
                return False
        wiki.updated_at = updated_at
        wiki.editor_generation = generation
        wiki.clear_changes()
        return True

    def get_all_by_project(self, project: TProjectParam):
        project_id = InfraHelper.convert_id(project)
        wikis = []
        with DbSession.use(readonly=False) as db:
            result = db.exec(
                SqlBuilder.select.table(ProjectWiki)
                .where(ProjectWiki.column("project_id") == project_id)
                .order_by(ProjectWiki.column("order").asc())
            )
            wikis = result.all()
        return wikis

    def get_by_project_and_uids(self, project: TProjectParam, wiki_uids: set[str]) -> list[ProjectWiki]:
        if not wiki_uids:
            return []
        project_id = InfraHelper.convert_id(project)
        wiki_ids = [InfraHelper.convert_id(wiki_uid) for wiki_uid in wiki_uids]
        with DbSession.use(readonly=False) as db:
            return db.exec(
                SqlBuilder.select.table(ProjectWiki)
                .where(ProjectWiki.column("project_id") == project_id)
                .where(ProjectWiki.column("id").in_(wiki_ids))
            ).all()

    def get_headers_by_project_and_uids(
        self, project: TProjectParam, wiki_uids: set[str]
    ) -> list[tuple[SnowflakeID, str, bool]]:
        """Project only the fields needed by the board, never the Wiki body."""

        if not wiki_uids:
            return []
        project_id = InfraHelper.convert_id(project)
        wiki_ids = [InfraHelper.convert_id(wiki_uid) for wiki_uid in wiki_uids]
        with DbSession.use(readonly=True) as db:
            rows = db.exec(
                SqlBuilder.select.columns(ProjectWiki.id, ProjectWiki.title, ProjectWiki.is_public)
                .where(ProjectWiki.column("project_id") == project_id)
                .where(ProjectWiki.column("id").in_(wiki_ids))
            ).all()
            return [(SnowflakeID(row[0]), row[1], row[2]) for row in rows]
