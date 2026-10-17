"""Isolated timer databases; real parent rows preserve foreign-key coverage."""

import os
from uuid import uuid4
import pytest
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Card,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    Organization,
    Project,
    ProjectColumn,
    User,
)
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def timer_engine(monkeypatch, request):
    url = request.param
    if url == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = "timer_batch_test_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    try:
        for model in (User, Organization, Project, ProjectColumn, Card, Checklist, Checkitem, CheckitemTimerRecord):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        with Session(engine) as db:
            db.add(User(id=2, firstname="Timer", lastname="Owner", email="timer@example.invalid", password="test-only"))
            db.commit()
            db.add(Project(id=10, owner_id=2, title="Timer board"))
            db.commit()
            db.add(ProjectColumn(id=40, project_id=10, name="Active"))
            db.commit()
        yield engine
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
