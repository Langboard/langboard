"""Column reads remain bounded and never repair archive storage as a side effect."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Card, Project, ProjectColumn, User
from langboard_shared.infrastructure.repositories.factory.ProjectColumnRepository import ProjectColumnRepository
from sqlalchemy import create_engine, event


@pytest.mark.parametrize("project_count", [1, 25])
@pytest.mark.parametrize("has_archive,limit", [(False, None), (True, 1), (True, 0)])
def test_column_reads_do_not_repair_missing_or_unreturned_archives(monkeypatch, project_count, has_archive, limit):
    engine = create_engine("sqlite://")
    for model in (User, Project, ProjectColumn, Card):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        projects = []
        with DbSession.use(readonly=False) as db:
            owner = User(firstname="Read", lastname="Test", email="read@example.invalid", password="test-only")
            db.insert(owner)
            for index in range(project_count):
                project = Project(owner_id=owner.id, title=f"Board {index}")
                db.insert(project)
                projects.append(project)
                db.insert(ProjectColumn(project_id=project.id, name="Active", order=0))
                if has_archive:
                    db.insert(ProjectColumn(project_id=project.id, name="Archive", order=1, is_archive=True))

        statements = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        repository = ProjectColumnRepository(lambda _: None, lambda _: None)
        rows = repository.get_all_by_project(projects, limit=limit)
        event.remove(engine, "before_cursor_execute", record)
        assert len(statements) == 1
        assert statements[0].lstrip().upper().startswith("SELECT")
        assert len(rows) == (project_count if limit is None else limit)
        assert all(not column.is_archive and count == 0 for column, count in rows)
        with DbSession.use(readonly=True) as db:
            archives = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.is_archive.is_(True))).all()
        assert len(archives) == (project_count if has_archive else 0)

        # The explicit write boundary still repairs a missing archive on first use.
        archive = repository.get_or_create_archive_if_not_exists(projects[0])
        assert archive.is_archive and archive.project_id == projects[0].id
        assert repository.get_or_create_archive_if_not_exists(projects[0]).id == archive.id
    finally:
        engine.dispose()
