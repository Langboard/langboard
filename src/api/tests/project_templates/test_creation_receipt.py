"""Native project creation retry contracts on real SQLite and disposable PostgreSQL."""

import importlib
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models
from langboard_shared.domain.models.ProjectCreationReceipt import ProjectCreationReceipt
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.ProjectTemplateService import (
    ProjectCreationUnavailable,
)
from langboard_shared.Env import Env
from langboard_shared.publishers import ProjectColumnPublisher
from langboard_shared.tasks.activities import ProjectActivityTask, ProjectColumnActivityTask
from langboard_shared.tasks.bots import ProjectColumnBotTask
from sqlalchemy import create_engine, delete, select, text, update


@pytest.fixture(params=["sqlite", "postgresql"])
def native(request, monkeypatch, tmp_path):
    schema = None
    admin = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_TEMPLATE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Owned disposable PostgreSQL URL not provided")
        admin = create_engine(url)
        schema = "creation_receipt_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'native.db'}", connect_args={"timeout": 20})
    required = {
        model.__table__
        for model in (
            models.User,
            models.Card,
            models.Project,
            models.ProjectColumn,
            models.ProjectLabel,
            models.ProjectAssignedUser,
            models.ProjectRole,
            models.InternalBot,
            models.ProjectAssignedInternalBot,
            models.ProjectTemplate,
            models.GlobalLabel,
            models.Bot,
            models.BotDefaultScopeBranch,
            models.ProjectBotScope,
            models.ProjectColumnBotScope,
            models.WorkflowStageDefinition,
            models.ProjectEmailNotificationPolicy,
            models.ProjectEmailNotificationRecipient,
        )
    }
    pending = list(required)
    while pending:
        for key in pending.pop().foreign_keys:
            if key.column.table not in required:
                required.add(key.column.table)
                pending.append(key.column.table)
    models.Project.metadata.create_all(engine, tables=list(required))
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261022200000-7248de96ca10.py"
    spec = importlib.util.spec_from_file_location("creation_receipt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    effects = []
    monkeypatch.setattr(ProjectActivityTask, "project_created", lambda *_: effects.append("project"))
    monkeypatch.setattr(ProjectColumnPublisher, "created", lambda *_: effects.append("column"))
    monkeypatch.setattr(ProjectColumnActivityTask, "project_column_created", lambda *_: None)
    monkeypatch.setattr(ProjectColumnBotTask, "project_column_created", lambda *_: None)
    with DbSession.atomic() as db:
        actor = models.User(
            firstname="Receipt",
            lastname="Owner",
            email="receipt@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        other = models.User(
            firstname="Other",
            lastname="Owner",
            email="other@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(actor)
        db.insert(other)
        db.insert(
            models.ProjectTemplate(name="Receipt template", columns=[{"name": "Inbox", "description": "Keep column"}])
        )
    service = DomainService()
    try:
        yield engine, actor, other, service, effects, migration
    finally:
        service.close()
        engine.dispose()
        if admin:
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


def create(service, actor, request_uuid, **changes):
    return service.project_template.create_project(
        actor,
        **{
            "title": "Exact source title",
            "description": "Exact source body",
            "project_type": "Other",
            "template_name": "Receipt template",
            "request_uuid": request_uuid,
            **changes,
        },
    )


def snapshot(engine):
    with engine.connect() as connection:
        return {
            model.__tablename__: connection.execute(select(model.__table__)).all()
            for model in (
                models.Project,
                models.ProjectColumn,
                models.ProjectLabel,
                models.ProjectRole,
                models.ProjectAssignedUser,
                ProjectCreationReceipt,
            )
        }


def test_commit_response_failure_retries_original_native_http_result(native, monkeypatch):
    engine, actor, _, service, effects, migration = native
    module = importlib.import_module("langboard.routes.dashboard.DashboardApi")
    middleware = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")

    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(middleware.ApiAuthMiddleware, routes=AppRouter.api.routes)
    key = str(uuid4())
    payload = {
        "title": "Exact source title",
        "description": "Exact source body",
        "project_type": "Other",
        "template_name": "Receipt template",
        "request_uuid": key,
    }
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/dashboard/projects/new", json=payload).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        client.headers["Authorization"] = f"Bearer {access}"
        original_response = module.JsonResponse
        with monkeypatch.context() as failure:
            failure.setattr(
                module, "JsonResponse", lambda **_: (_ for _ in ()).throw(RuntimeError("response lost after commit"))
            )
            assert client.post("/dashboard/projects/new", json=payload).status_code == 500
        assert module.JsonResponse is original_response
        before = snapshot(engine)
        before_effects = list(effects)
        response = client.post("/dashboard/projects/new", json=payload)
        assert response.status_code == 201, response.text
        with engine.connect() as connection:
            project = connection.execute(select(models.Project.__table__)).one()
        assert response.json() == {"project_uid": models.Project.model_validate(dict(project._mapping)).get_uid()}
        assert project.title == payload["title"] and project.description == payload["description"]
        assert snapshot(engine) == before
        assert effects == before_effects == ["project", "column"]
        changed = client.post("/dashboard/projects/new", json={**payload, "description": "Changed payload"})
        assert changed.status_code == 409, changed.text
        assert client.post("/dashboard/projects/new", json={**payload, "request_uuid": "invalid"}).status_code == 400
        assert snapshot(engine) == before
        # Legacy callers retain their native create-per-request behavior.
        legacy = {key: value for key, value in payload.items() if key != "request_uuid"}
        assert client.post("/dashboard/projects/new", json=legacy).status_code == 201
        assert client.post("/dashboard/projects/new", json=legacy).status_code == 201
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
    assert len(snapshot(engine)["project"]) == 3


def test_concurrent_requests_create_one_board_and_hooks_once(native):
    engine, actor, other, _, effects, _ = native
    key = str(uuid4())
    barrier = Barrier(2)

    def submit(_):
        service = DomainService()
        try:
            barrier.wait(timeout=10)
            return create(service, actor, key)[0].get_uid()
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, range(2)))
    assert result[0] == result[1]
    assert len(snapshot(engine)["project"]) == 1
    assert effects == ["project", "column"]
    service = DomainService()
    try:
        assert create(service, other, key)[0].get_uid() != result[0]
    finally:
        service.close()
    assert len(snapshot(engine)["project"]) == 2


@pytest.mark.parametrize(
    "revocation",
    ["deleted_project", "purged_project", "membership", "read_role", "deleted_account", "inactive_account"],
)
def test_replay_rechecks_current_authority_and_never_resurrects(native, revocation):
    engine, actor, other, service, effects, _ = native
    key = str(uuid4())
    project = create(service, actor, key)[0]
    with engine.begin() as connection:
        if revocation == "deleted_project":
            connection.execute(
                update(models.Project).where(models.Project.id == project.id).values(deleted_at=SafeDateTime.now())
            )
        elif revocation == "purged_project":
            connection.execute(delete(models.Project).where(models.Project.id == project.id))
        elif revocation in ("membership", "read_role"):
            connection.execute(update(models.Project).where(models.Project.id == project.id).values(owner_id=other.id))
            if revocation == "read_role":
                connection.execute(
                    update(models.ProjectRole).where(models.ProjectRole.project_id == project.id).values(actions=[])
                )
            else:
                connection.execute(
                    delete(models.ProjectAssignedUser).where(models.ProjectAssignedUser.project_id == project.id)
                )
        else:
            connection.execute(
                update(models.User)
                .where(models.User.id == actor.id)
                .values(
                    **(
                        {"deleted_at": SafeDateTime.now()}
                        if revocation == "deleted_account"
                        else {"activated_at": None}
                    )
                )
            )
    before = snapshot(engine)
    with pytest.raises(ProjectCreationUnavailable):
        create(service, actor, key)
    assert snapshot(engine) == before
    assert effects == ["project", "column"]


def test_native_rollback_removes_receipt_and_hooks_before_retry(native, monkeypatch):
    engine, actor, _, service, effects, _ = native
    key = str(uuid4())
    original = DbSession.insert

    def fail_receipt(db, item):
        original(db, item)
        if isinstance(item, ProjectCreationReceipt):
            raise RuntimeError("receipt failure after native writes")

    with monkeypatch.context() as failure:
        failure.setattr(DbSession, "insert", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt failure"):
            create(service, actor, key)
    assert all(not rows for rows in snapshot(engine).values())
    assert effects == []
    project = create(service, actor, key)[0]
    assert create(service, actor, key)[0].get_uid() == project.get_uid()
    assert effects == ["project", "column"]


def test_additive_receipt_migration_preserves_existing_native_rows(native, monkeypatch):
    engine, actor, _, service, _, migration = native
    project = create(service, actor, None)[0]
    before = snapshot(engine)
    with engine.connect() as connection:
        users = connection.execute(select(models.User.__table__)).all()
        templates = connection.execute(select(models.ProjectTemplate.__table__)).all()
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
        migration.upgrade()
    assert snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.execute(select(models.User.__table__)).all() == users
        assert connection.execute(select(models.ProjectTemplate.__table__)).all() == templates
    assert create(service, actor, str(uuid4()))[0].get_uid() != project.get_uid()


def test_receipt_replay_outlives_removed_template_without_recreating_it(native):
    engine, actor, _, service, effects, _ = native
    key = str(uuid4())
    project, columns, template = create(service, actor, key)
    with engine.begin() as connection:
        connection.execute(delete(models.ProjectTemplate).where(models.ProjectTemplate.id == template.id))
    before = snapshot(engine)
    replayed, replayed_columns, replayed_template = create(service, actor, key)
    assert replayed.get_uid() == project.get_uid()
    assert [column.id for column in replayed_columns] == [column.id for column in columns]
    assert replayed_template is None
    assert snapshot(engine) == before
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(models.ProjectTemplate.id).where(models.ProjectTemplate.id == template.id)
            ).first()
            is None
        )
    assert effects == ["project", "column"]


@pytest.mark.parametrize("handler_name", ["create_project", "create_project_board"])
def test_mcp_handlers_reuse_native_receipt_and_survive_removed_template(native, handler_name):
    from langboard.mcp_integration.BoardOutputs import BOARD_OUTPUTS
    from langboard.mcp_tools import ProjectMcp
    from langboard_shared.domain.services.factory.ProjectTemplateService import ProjectCreationConflict

    engine, actor, _, service, effects, _ = native
    handler = getattr(ProjectMcp, handler_name)
    payload = dict(
        title="Exact source title",
        description="Exact source body",
        template_name="Receipt template",
        user=actor,
        service=service,
        request_uuid=str(uuid4()),
    )
    if handler_name == "create_project":
        payload["project_type"] = "Other"
    original = handler(**payload)
    before = snapshot(engine)
    repeated = handler(**payload)
    assert repeated.get("project_uid", repeated.get("project")) == original.get("project_uid", original.get("project"))
    if handler_name == "create_project_board":
        assert [column["uid"] for column in repeated["columns"]] == [column["uid"] for column in original["columns"]]
    assert len(before["project"]) == 1
    with pytest.raises(ProjectCreationConflict):
        handler(**{**payload, "description": "Changed payload"})
    assert snapshot(engine) == before
    with engine.begin() as connection:
        connection.execute(delete(models.ProjectTemplate).where(models.ProjectTemplate.name == "Receipt template"))
    replayed = handler(**payload)
    if handler_name == "create_project_board":
        assert replayed["project"] == {**original["project"], "template": ""}
        assert BOARD_OUTPUTS[handler_name].model_validate(replayed).project.template == ""
    else:
        assert replayed == original
    assert snapshot(engine) == before
    assert effects == ["project", "column"]


@pytest.mark.parametrize("revocation", ["deleted_account", "inactive_account", "read_role", "membership"])
def test_mcp_receipt_does_not_disclose_after_native_authority_revocation(native, revocation):
    from langboard.mcp_tools import ProjectMcp

    engine, actor, other, service, effects, _ = native
    payload = dict(
        title="Board", template_name="Receipt template", user=actor, service=service, request_uuid=str(uuid4())
    )
    ProjectMcp.create_project_board(**payload)
    with engine.begin() as connection:
        if revocation in ("deleted_account", "inactive_account"):
            connection.execute(
                update(models.User)
                .where(models.User.id == actor.id)
                .values(
                    **(
                        {"deleted_at": SafeDateTime.now()}
                        if revocation == "deleted_account"
                        else {"activated_at": None}
                    )
                )
            )
        else:
            connection.execute(update(models.Project).values(owner_id=other.id))
            if revocation == "membership":
                connection.execute(delete(models.ProjectAssignedUser))
            else:
                connection.execute(update(models.ProjectRole).values(actions=[]))
    before = snapshot(engine)
    with pytest.raises(ProjectCreationUnavailable):
        ProjectMcp.create_project_board(**payload)
    assert snapshot(engine) == before
    assert effects == ["project", "column"]
