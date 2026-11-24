"""Related-project recency uses current assignments without scanning event history."""
import os
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Project
from langboard_shared.infrastructure.repositories.factory.ProjectRepository import ProjectRepository
from sqlalchemy import create_engine, event, select, text


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def activity_db(request, monkeypatch):
    url = request.param
    if url == "postgresql-test":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    schema = None
    try:
        if engine.dialect.name == "postgresql":
            schema = "related_activity_" + uuid4().hex
            with engine.begin() as db:
                db.execute(text(f'CREATE SCHEMA "{schema}"'))

            @event.listens_for(engine, "begin")
            def use_schema(connection):
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')

        # Minimal projections of the existing tables: no product data or writes.
        with engine.begin() as db:
            for ddl in (
                "CREATE TABLE project(id BIGINT PRIMARY KEY)",
                "CREATE TABLE card(id BIGINT PRIMARY KEY,project_id BIGINT,archived_at TIMESTAMP,deleted_at TIMESTAMP)",
                "CREATE TABLE card_assigned_user(card_id BIGINT,user_id BIGINT)",
                "CREATE TABLE project_activity(id BIGINT PRIMARY KEY,card_id BIGINT,created_at TIMESTAMP)",
                "CREATE INDEX ix_project_activity_card_id_created_at ON project_activity(card_id,created_at)",
                "INSERT INTO project VALUES (1),(2),(3),(4),(5),(6)",
                "INSERT INTO card VALUES (11,1,NULL,NULL),(12,1,NULL,NULL),(13,1,'2026-01-01',NULL),"
                "(14,1,NULL,'2026-01-01'),(21,2,NULL,NULL),(31,3,NULL,NULL),(41,4,NULL,NULL),"
                "(51,5,NULL,'2026-01-01'),(61,6,'2026-01-01',NULL)",
                "INSERT INTO card_assigned_user VALUES (11,7),(12,7),(13,7),(14,7),(21,8),(31,7),(51,7),(61,7)",
                "INSERT INTO project_activity VALUES (1,11,'2026-01-01'),(2,11,'2026-01-02'),"
                "(3,12,'2026-01-03'),(4,13,'2026-01-09'),(5,14,'2026-01-10'),(6,21,'2026-01-11')",
            ):
                db.execute(text(ddl))
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        yield engine
    finally:
        engine.dispose()
        if schema:
            cleanup = create_engine(url)
            try:
                with cleanup.begin() as db:
                    db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            finally:
                cleanup.dispose()


@pytest.mark.parametrize("user_id,expected", [(7, {1: "2026-01-03"}), (8, {2: "2026-01-11"}), (9, {})])
def test_related_recency_preserves_assignment_archive_deletion_and_empty_projects(activity_db, user_id, expected):
    related = ProjectRepository._related_activity_by_project(user_id)
    query = (
        select(Project.id, related.c.related_activity_at, ProjectRepository._related_to_user(user_id))
        .outerjoin(related, related.c.project_id == Project.id)
        .order_by(Project.id)
    )
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(activity_db, "before_cursor_execute", capture)
    try:
        with DbSession.use(readonly=True) as db:
            rows = db.exec(query).all()
    finally:
        event.remove(activity_db, "before_cursor_execute", capture)
    assert [int(project_id) for project_id, _, _ in rows] == [1, 2, 3, 4, 5, 6]
    assert {int(project_id): str(timestamp)[:10] for project_id, timestamp, _ in rows if timestamp} == expected
    expected_related = {7: {1, 3}, 8: {2}, 9: set()}[user_id]
    assert {int(project_id) for project_id, _, is_related in rows if is_related} == expected_related
    assert len(statements) == 1
    assert "JOIN project_activity ON" not in statements[0]
    assert "WHERE project_activity.card_id = card.id" in statements[0]
