"""The card context carries the current execution fence in its one response."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.routes.board import BoardCardApi
from langboard_shared.core.routing import ApiException


def test_card_context_includes_ready_generation_and_rejects_mixed_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = datetime(2026, 9, 25, tzinfo=timezone.utc)
    card = SimpleNamespace(id=42)
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=lambda *args: (object(), card, object())))
    request = SimpleNamespace(scope={}, query_params={})
    adapter = SimpleNamespace(
        task_content={"title": "Task", "description": "Body", "labels": ["backend"], "assignee_ids": [123]}
    )
    monkeypatch.setattr(BoardCardApi, "NativeCardWorkspaceAdapter", lambda *_args, **_kwargs: adapter)
    bundle = Mock(
        return_value=SimpleNamespace(
            model_dump=lambda **_kwargs: {"card": {"core": {"uid": "card", "updated_at": revision.isoformat()}}}
        )
    )
    fence = Mock(return_value=SimpleNamespace(revision=revision, is_ready=True, generation=5, **adapter.task_content))
    monkeypatch.setattr(BoardCardApi, "get_card_bundle", bundle)
    monkeypatch.setattr(BoardCardApi, "current_execution", fence)
    monkeypatch.setattr(BoardCardApi, "receipt_history", lambda _card_id: [])

    response = BoardCardApi.get_card_context("project", "card", object(), service, request=request)
    assert json.loads(response.body)["scope_context"]["card"]["execution"] == {
        "is_ready": True,
        "generation": 5,
    }
    assert bundle.call_count == 1
    fence.assert_called_once_with(42, include_description=True)

    fence.return_value.revision = revision + timedelta(seconds=1)
    with pytest.raises(ApiException.Conflict_409) as conflict:
        BoardCardApi.get_card_context("project", "card", object(), service, request=request)
    assert conflict.value.status_code == 409


def test_unreadable_details_context_and_comments_return_not_found_before_sections(monkeypatch):
    card_service = SimpleNamespace(
        resolve_readable_card=Mock(return_value=None),
        resolve_canonical_card=Mock(return_value=None),
        get_details=Mock(),
    )
    service = SimpleNamespace(card=card_service, card_comment=SimpleNamespace(get_api_list_by_card=Mock()))
    request = SimpleNamespace(scope={}, query_params={})
    bundle = Mock()
    monkeypatch.setattr(BoardCardApi, "get_card_bundle", bundle)
    for callback in (
        lambda: BoardCardApi.get_card_details("p", "c", request, object(), service),
        lambda: BoardCardApi.get_card_context("p", "c", object(), service, request=request),
        lambda: BoardCardApi.get_card_comments("p", "c", request, object(), service),
    ):
        with pytest.raises(ApiException.NotFound_404):
            callback()
    card_service.get_details.assert_not_called()
    service.card_comment.get_api_list_by_card.assert_not_called()
    bundle.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [("labels", ["renamed"]), ("assignee_ids", [456]), ("description", "New body"), ("title", "New title")],
)
def test_unchanged_card_revision_rejects_divergent_task_semantics(monkeypatch, field, value):
    revision = datetime(2026, 9, 25, tzinfo=timezone.utc)
    observed = {"title": "Task", "description": "Body", "labels": ["backend"], "assignee_ids": [123]}
    adapter = SimpleNamespace(task_content=observed)
    service = SimpleNamespace(
        card=SimpleNamespace(resolve_readable_card=lambda *args: (object(), SimpleNamespace(id=42), object()))
    )
    monkeypatch.setattr(BoardCardApi, "NativeCardWorkspaceAdapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        BoardCardApi,
        "get_card_bundle",
        lambda *args: SimpleNamespace(
            model_dump=lambda **kwargs: {"card": {"core": {"updated_at": revision.isoformat()}}}
        ),
    )
    current = SimpleNamespace(revision=revision, is_ready=True, generation=5, **{**observed, field: value})
    monkeypatch.setattr(BoardCardApi, "current_execution", lambda *args, **kwargs: current)
    receipts = Mock(return_value=[])
    monkeypatch.setattr(BoardCardApi, "receipt_history", receipts)
    with pytest.raises(ApiException.Conflict_409):
        BoardCardApi.get_card_context("project", "card", object(), service, request=SimpleNamespace(scope={}))
    receipts.assert_not_called()


@pytest.mark.parametrize("encoded_description", [False, True])
def test_primary_snapshot_query_includes_body_without_changing_delivery_read(monkeypatch, encoded_description):
    from contextlib import contextmanager
    from importlib import import_module
    from sqlalchemy.dialects import postgresql

    module = import_module("langboard_shared.tasks.webhooks.ExecutionReadinessUow")
    revision = datetime(2026, 9, 25, tzinfo=timezone.utc)
    statements = []
    body = {"content": "Body"}

    class Transport:
        def exec(self, statement, **kwargs):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            statements.append(sql)
            row = (revision, "Task", ["backend"], [123], True, 5)
            if "c.description" in sql:
                row += (json.dumps(body) if encoded_description else body,)
            return SimpleNamespace(first=lambda: row)

    @contextmanager
    def primary(*, readonly):
        assert readonly is False
        yield Transport()

    monkeypatch.setattr(module.DbSession, "use", primary)
    task = module.current_execution(42, include_description=True)
    assert task.description == "Body"
    assert (task.labels, task.assignee_ids, task.is_ready, task.generation) == (["backend"], [123], True, 5)
    assert len(statements) == 1
    assert all(
        fragment in statements[0]
        for fragment in (
            "c.description",
            "label.name",
            "assigned.user_id",
            "g.execution_generation",
            "project_execution_binding",
        )
    )
    module.current_execution(42)
    assert "c.description" not in statements[1]


@pytest.mark.parametrize("renamed_id", [9, 34])
def test_real_label_store_divergence_at_unchanged_revision_fails_closed(monkeypatch, renamed_id):
    """SQLite stores exercise actual rename/replica lag; PG transport is substituted."""
    import sqlite3
    from langboard_shared.core.types import SnowflakeID

    revision = datetime(2026, 9, 25, tzinfo=timezone.utc)
    primary = sqlite3.connect(":memory:")
    replica = sqlite3.connect(":memory:")
    for database in (primary, replica):
        database.execute("CREATE TABLE project_label(id integer, name text)")
        database.execute("INSERT INTO project_label VALUES (9, 'backend')")
        database.executemany(
            "INSERT INTO project_label VALUES (?, ?)", [(9 + i, f"label-{i:02d}") for i in range(1, 26)]
        )
    card = SimpleNamespace(
        id=42,
        project_id=1,
        project_column_id=2,
        created_by_user_id=None,
        created_by_bot_id=None,
        api_response=lambda: {
            "uid": "card",
            "title": "Task",
            "description": {"content": "Body"},
            "updated_at": revision.isoformat(),
        },
    )
    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_readable_card=lambda *args: (SimpleNamespace(id=1), card, object()),
            can_delete=lambda *args: False,
            is_check_card=lambda *args: False,
            _get_completion_checklist=lambda *args: None,
            get_work_states=lambda *args, **kwargs: {42: {"version": 1}},
            get_api_assigned_user_list=lambda *args, **kwargs: [
                {"uid": SnowflakeID(123).to_short_code(), "firstname": "Test"}
            ],
            get_active_workers=lambda *args: {42: []},
        ),
        project_column=SimpleNamespace(
            get_by_id_like=lambda *args: SimpleNamespace(id=2, project_id=1, name="Ready"),
            get_workflow_guidance=lambda *args: {2: {}},
        ),
        project_label=SimpleNamespace(
            get_api_list_by_card=lambda *args, **kwargs: [
                {"uid": str(row[0]), "name": row[1]}
                for row in replica.execute("SELECT id, name FROM project_label ORDER BY id")
            ]
        ),
        card_relationship=SimpleNamespace(get_api_list_by_card=lambda *args, **kwargs: []),
        checklist=SimpleNamespace(get_api_list_by_card=lambda *args, **kwargs: []),
        card_attachment=SimpleNamespace(get_api_list_by_card=lambda *args, **kwargs: []),
        metadata=SimpleNamespace(get_all_as_api=lambda *args, **kwargs: {}),
    )
    monkeypatch.setattr(BoardCardApi, "receipt_history", lambda *args: [])
    monkeypatch.setattr(
        BoardCardApi,
        "current_execution",
        lambda *args, **kwargs: SimpleNamespace(
            revision=revision,
            title="Task",
            description="Body",
            labels=[row[0] for row in primary.execute("SELECT name FROM project_label ORDER BY id")],
            assignee_ids=[123],
            is_ready=True,
            generation=5,
        ),
    )
    request = SimpleNamespace(scope={})
    try:
        response = BoardCardApi.get_card_context("project", "card", object(), service, request=request)
        assert (
            json.loads(response.body)["scope_context"]["card"]["classification"]["labels"]["items"][0]["name"]
            == "backend"
        )
        # ProjectLabel rename commits independently. Card.updated_at stays equal.
        primary.execute("UPDATE project_label SET name = 'renamed' WHERE id = ?", (renamed_id,))
        primary.commit()
        with pytest.raises(ApiException.Conflict_409):
            BoardCardApi.get_card_context("project", "card", object(), service, request=request)
        replica.execute("UPDATE project_label SET name = 'renamed' WHERE id = ?", (renamed_id,))
        response = BoardCardApi.get_card_context("project", "card", object(), service, request=request)
        result = json.loads(response.body)["scope_context"]["card"]
        assert result["classification"]["labels"]["items"][0]["name"] == ("renamed" if renamed_id == 9 else "backend")
        assert len(result["classification"]["labels"]["items"]) == 10
        assert result["classification"]["labels"]["total_count"] == 26
        assert result["execution"] == {"is_ready": True, "generation": 5}
    finally:
        primary.close()
        replica.close()


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("mutation", ["label-visible", "label-beyond-page", "description"])
def test_postgres_native_context_rejects_unchanged_revision_content_divergence(board, monkeypatch, mutation):
    """Real PG statements, native route/adapter/bundle; independent schema models lag.

    The shared disposable board fixture owns primary isolation and cleanup. Service
    fixtures query real card/label/assignee rows; ancillary sections are empty.
    This is neither streaming replication nor authenticated consumer/Studio E2E.
    """
    from uuid import uuid4
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.tasks.webhooks.ExecutionReadinessUow import current_execution
    from sqlalchemy import create_engine, event, text

    _, actor, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    revision = datetime(2026, 9, 25, tzinfo=timezone.utc)
    replica_schema = "context_snapshot_" + uuid4().hex
    statements = []

    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    replica = None
    try:
        with engine.begin() as db:
            for sql in (
                "CREATE TABLE card (id bigint PRIMARY KEY, project_id bigint, project_column_id bigint, "
                "title text, description jsonb, updated_at timestamptz, deleted_at timestamptz, "
                "archived_at timestamptz, source_type text)",
                "CREATE TABLE project_label (id bigint PRIMARY KEY, name text)",
                "CREATE TABLE card_assigned_project_label (card_id bigint, project_label_id bigint)",
                "CREATE TABLE card_assigned_user (card_id bigint, user_id bigint)",
                "CREATE TABLE card_execution_generation (card_id bigint PRIMARY KEY, execution_generation bigint)",
                "CREATE TABLE global_card_relationship_type (id bigint PRIMARY KEY, machine_semantic text, is_active boolean)",
                "CREATE TABLE card_relationship (card_id_parent bigint, card_id_child bigint, relationship_type_id bigint)",
                "CREATE TABLE webhook_setting (id bigint PRIMARY KEY, secret_id bigint, events jsonb)",
                "CREATE TABLE project_execution_binding (project_id bigint, webhook_id bigint, "
                "prerequisite_relationship_type_id bigint, is_enabled boolean, events jsonb, column_semantic_ids jsonb)",
            ):
                db.execute(text(sql))
            db.execute(text("INSERT INTO card VALUES (100, :project, :column, 'Task', "
                            "CAST(:body AS jsonb), :revision, NULL, NULL, NULL)"),
                       {"project": int(project.id), "column": int(columns[0].id),
                        "body": json.dumps({"content": "Original body 한글"}), "revision": revision})
            db.execute(text("INSERT INTO card_execution_generation VALUES (100, 5)"))
            db.execute(text("INSERT INTO global_card_relationship_type VALUES (30, 'blocks', true)"))
            db.execute(text("INSERT INTO webhook_setting VALUES (40, 50, CAST(:events AS jsonb))"), {"events": '["io.langboard.work.ready.v1"]'})
            db.execute(text("INSERT INTO project_execution_binding VALUES (:project, 40, 30, true, "
                            "CAST(:events AS jsonb), CAST(:columns AS jsonb))"),
                       {"project": int(project.id), "events": '["io.langboard.work.ready.v1"]',
                        "columns": json.dumps({str(columns[0].id): "ready"})})
            db.execute(text("INSERT INTO project_label VALUES (:id, :name)"),
                       [{"id": i, "name": f"label-{i:02d}"} for i in range(9, 35)])
            db.execute(text("INSERT INTO card_assigned_project_label VALUES (100, :id)"),
                       [{"id": i} for i in range(9, 35)])
            db.execute(text("INSERT INTO card_assigned_user VALUES (100, :id)"), {"id": int(actor.id)})
            db.execute(text(f"CREATE SCHEMA {replica_schema}"))
            for table in ("card", "project_label", "card_assigned_project_label", "card_assigned_user"):
                db.execute(text(f"CREATE TABLE {replica_schema}.{table} AS TABLE {table}"))
        replica = create_engine(engine.url, connect_args={"options": f"-csearch_path={replica_schema}"})

        def resolved(*args):
            with replica.connect() as db:
                row = db.execute(text("SELECT * FROM card WHERE id=100")).mappings().one()
            card = SimpleNamespace(
                id=100, project_id=project.id, project_column_id=columns[0].id,
                created_by_user_id=None, created_by_bot_id=None,
                api_response=lambda: {"uid": SnowflakeID(100).to_short_code(), "title": row["title"],
                                      "description": row["description"], "updated_at": row["updated_at"].isoformat()},
            )
            return project, card, object()

        def labels(*args, **kwargs):
            with replica.connect() as db:
                return [{"uid": str(row.id), "name": row.name} for row in db.execute(text(
                    "SELECT label.id, label.name FROM project_label label "
                    "JOIN card_assigned_project_label assigned ON assigned.project_label_id=label.id "
                    "WHERE assigned.card_id=100 ORDER BY label.id LIMIT :limit"), {"limit": kwargs["limit"]})]

        def people(*args, **kwargs):
            with replica.connect() as db:
                return [{"uid": SnowflakeID(row.user_id).to_short_code(), "firstname": "Fixture"}
                        for row in db.execute(text("SELECT user_id FROM card_assigned_user WHERE card_id=100"))]

        service = SimpleNamespace(
            card=SimpleNamespace(resolve_readable_card=resolved, can_delete=lambda *args: False,
                is_check_card=lambda *args: False, _get_completion_checklist=lambda *args: None,
                get_work_states=lambda *args, **kwargs: {100: {"version": 1}},
                get_api_assigned_user_list=people, get_active_workers=lambda *args: {100: []}),
            project_column=SimpleNamespace(get_by_id_like=lambda *args: columns[0],
                                          get_workflow_guidance=lambda *args: {columns[0].id: {}}),
            project_label=SimpleNamespace(get_api_list_by_card=labels),
            card_relationship=SimpleNamespace(get_api_list_by_card=lambda *args, **kwargs: []),
            checklist=SimpleNamespace(get_api_list_by_card=lambda *args, **kwargs: []),
            card_attachment=SimpleNamespace(get_api_list_by_card=lambda *args, **kwargs: []),
            metadata=SimpleNamespace(get_all_as_api=lambda *args, **kwargs: {}),
        )
        receipts = Mock(return_value=[])
        monkeypatch.setattr(BoardCardApi, "receipt_history", receipts)
        app = FastAPI()
        requests = []

        def point_read(request: Request):
            requests.append((request.method, request.url.path))
            return BoardCardApi.get_card_context(project.get_uid(), SnowflakeID(100).to_short_code(),
                                                 object(), service, request=request)

        path = f"/board/{project.get_uid()}/card/{SnowflakeID(100).to_short_code()}/context"
        app.add_api_route(path, point_read, methods=["GET"])
        client = TestClient(app)

        def context(expected_status=200):
            statements.clear()
            requests.clear()
            response = client.get(path)
            assert response.status_code == expected_status, response.text
            assert requests == [("GET", path)]
            assert sum("COALESCE(g.execution_generation, 0)" in sql for sql in statements) == 1
            if expected_status == 200:
                return response.json()["scope_context"]["card"]
            return response.json()

        baseline = context()
        assert baseline["execution"] == {"is_ready": True, "generation": 5}
        assert baseline["classification"]["labels"]["total_count"] == 26
        assert len(baseline["classification"]["labels"]["items"]) == 10
        before = current_execution(100, include_description=True)
        if mutation == "description":
            update = text("UPDATE card SET description=CAST(:body AS jsonb) WHERE id=100")
            params = {"body": json.dumps({"content": "Changed body 한국어"})}
        else:
            update = text("UPDATE project_label SET name='renamed' WHERE id=:id")
            params = {"id": 34 if mutation == "label-beyond-page" else 9}
        with engine.begin() as db:
            db.execute(update, params)
        after = current_execution(100, include_description=True)
        assert (after.revision, after.is_ready, after.generation) == (revision, True, 5)
        assert after != before
        assert current_execution(100).description is None
        receipts.reset_mock()
        context(expected_status=409)
        receipts.assert_not_called()
        with replica.begin() as db:
            db.execute(update, params)
        synchronized = context()
        assert synchronized["execution"] == {"is_ready": True, "generation": 5}
        assert synchronized["core"]["updated_at"] == baseline["core"]["updated_at"]
        if mutation == "description":
            assert "Changed body 한국어" in json.dumps(synchronized, ensure_ascii=False)
        elif mutation == "label-visible":
            assert synchronized["classification"]["labels"]["items"][0]["name"] == "renamed"
        else:
            assert "renamed" not in [row["name"] for row in synchronized["classification"]["labels"]["items"]]
        # A readiness-only change is represented by the same point-read response.
        with engine.begin() as db:
            db.execute(text("UPDATE project_execution_binding SET is_enabled=false"))
        assert context()["execution"] == {"is_ready": False, "generation": 5}
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        if "client" in locals():
            client.close()
        if replica is not None:
            replica.dispose()
        with engine.begin() as db:
            db.execute(text(f"DROP SCHEMA IF EXISTS {replica_schema} CASCADE"))
