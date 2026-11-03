"""Native outbox delivery over a real loopback socket and isolated PostgreSQL.

The receiver is a test fixture using the supported SDK verifier, not a production
consumer. It deliberately makes no claim about durable consumer deduplication.
"""

import asyncio
import json
import os
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateTable


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard_sdk import InvalidWebhook, verify_webhook  # noqa: E402
from langboard_shared.core.db import DbSession  # noqa: E402
from langboard_shared.core.db.DbEngine import DbEngine  # noqa: E402
from langboard_shared.domain.models import (  # noqa: E402
    GlobalCardRelationshipType,
    Project,
    ProjectColumn,
    ProjectExecutionBinding,
    WebhookSetting,
    WorkflowStageDefinition,
)
from langboard_shared.tasks.webhooks import ExecutionOutboxWorker as worker  # noqa: E402
from langboard_shared.tasks.webhooks import WebhookTask
from langboard_shared.tasks.webhooks.utils import ResolvedWebhookTarget  # noqa: E402


SECRET = "loopback-fixture-only-secret"
EVENT = "io.langboard.work.ready.v1"
DATABASE_URL = os.getenv("LANGBOARD_OUTBOX_TEST_DATABASE_URL")


@pytest.fixture
def receiver():
    state = SimpleNamespace(requests=[], failures=deque(), rejected=0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            headers = dict(self.headers.items())
            try:
                verify_webhook(body, headers, SECRET)
            except InvalidWebhook:
                state.rejected += 1
                status = 401
            else:
                state.requests.append((body, headers))
                status = state.failures.popleft() if state.failures else 204
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/native-execution"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.fixture
def signed_outbox(monkeypatch, receiver):
    if not DATABASE_URL:
        pytest.skip("dedicated PostgreSQL proof URL not set")
    schema = f"signed_http_{uuid4().hex}"
    admin = create_engine(DATABASE_URL)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(WebhookTask.KeyVault, "get_key", lambda _key: SECRET)
    # Suppress unrelated websocket publication after the real repository update.
    monkeypatch.setattr(WebhookTask.AppSettingPublisher, "webhook_setting_updated", lambda *_args: None)

    async def owned_loopback_only(url):
        assert url == receiver.url
        return ResolvedWebhookTarget(url=url, host_header=url.split("/")[2], sni_hostname="127.0.0.1")

    # Only outbound URL policy is replaced; signing, HTTP client, dispatch,
    # claim, current authority checks, and terminal persistence stay native.
    monkeypatch.setattr(WebhookTask, "ensure_public_webhook_url", owned_loopback_only)
    try:
        with engine.begin() as connection:
            # Reuse the isolated model-table pattern of test_execution_outbox_postgres.
            # Unrelated account foreign keys are outside this fixture.
            for model in (Project, ProjectColumn, GlobalCardRelationshipType,
                          ProjectExecutionBinding, WebhookSetting, WorkflowStageDefinition):
                connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
            for statement in (
                "CREATE TABLE card (id bigint PRIMARY KEY, project_id bigint, project_column_id bigint, "
                "deleted_at timestamptz, archived_at timestamptz, source_type text, title text, updated_at timestamptz)",
                "CREATE TABLE card_relationship (card_id_parent bigint, card_id_child bigint, relationship_type_id bigint)",
                "CREATE TABLE project_label (id bigint PRIMARY KEY, name text)",
                "CREATE TABLE card_assigned_project_label (card_id bigint, project_label_id bigint)",
                "CREATE TABLE card_assigned_user (card_id bigint, user_id bigint)",
                "CREATE TABLE card_execution_generation (card_id bigint PRIMARY KEY, execution_generation integer)",
                "CREATE TABLE execution_outbox (id uuid PRIMARY KEY, project_id bigint, card_id bigint, "
                "execution_generation integer, occurred_at timestamptz DEFAULT now(), event_type text, "
                "payload_json jsonb, state text DEFAULT 'pending', last_error text, processed_at timestamptz, "
                "attempt_count integer NOT NULL DEFAULT 0, lease_until timestamptz)",
            ):
                connection.execute(text(statement))
        ready = ProjectColumn(id=20, project_id=7, name="Ready")
        terminal = ProjectColumn(id=21, project_id=7, name="Done")
        relation = GlobalCardRelationshipType(id=30, parent_name="Blocks", child_name="Blocked by",
                                              machine_semantic="blocks")
        hook = WebhookSetting(id=40, name="Loopback", url=receiver.url, secret_id="fixture-key", events=[EVENT])
        binding = ProjectExecutionBinding(
            id=8, project_id=7, is_enabled=True, events=[EVENT],
            column_semantics={ready.get_uid(): "ready", terminal.get_uid(): "terminal"},
            column_semantic_ids={"20": "ready", "21": "terminal"},
            prerequisite_relationship_type_id=30, prerequisite_relationship_type_uid=relation.get_uid(),
            webhook_id=40, webhook_uid=hook.get_uid(),
        )
        event_id = uuid4()
        frozen = {
            "title": "Frozen title", "labels": ["frozen-role"], "assignee_ids": [],
            "source_revision": "2026-09-24T00:00:00+00:00", "binding_id": "8",
            "binding_revision": binding.updated_at.isoformat(), "webhook_uid": hook.get_uid(),
        }
        with DbSession.atomic() as db:
            for record in (Project(id=7, owner_id=123, title="Fixture"), ready, terminal, relation, hook, binding):
                db.exec(record.__table__.insert().values(record.model_dump()))
            db.exec(text("INSERT INTO card VALUES "
                         "(100, 7, 20, NULL, NULL, NULL, 'Current edited title', '2026-10-01T00:00:00Z')"))
            db.exec(text("INSERT INTO card_execution_generation VALUES (100, 3)"))
            db.exec(text("INSERT INTO execution_outbox "
                         "(id, project_id, card_id, execution_generation, event_type, payload_json) "
                         "VALUES (:id, 7, 100, 3, :event, CAST(:payload AS jsonb))"),
                    params={"id": event_id, "event": EVENT, "payload": json.dumps(frozen)})
        yield engine, event_id, frozen
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


@pytest.mark.parametrize("failures", [[], [500], [500, 500, 500, 500]], ids=["success", "retry", "exhaustion"])
def test_native_signed_http_reaches_persisted_terminal_with_frozen_identity(signed_outbox, receiver, failures):
    engine, event_id, frozen = signed_outbox
    receiver.failures.extend(failures)

    def persisted():
        # Independent connection/session observes every committed transition.
        with engine.connect() as connection:
            return connection.execute(text(
                "SELECT state, attempt_count, last_error, payload_json, processed_at FROM execution_outbox"
            )).one()

    for attempt in range(1, len(failures) + 1):
        with pytest.raises(worker.ExecutionDeliveryFailed):
            asyncio.run(worker.drain_one(event_id))
        row = persisted()
        exhausted = attempt == worker.MAX_DELIVERY_ATTEMPTS
        assert row.state == ("failed" if exhausted else "pending")
        assert row.attempt_count == attempt
        assert row.last_error == ("delivery_attempts_exhausted" if exhausted else "WebhookDeliveryError")
        assert row.payload_json == frozen
        assert row.processed_at is not None

    exhausted = len(failures) == worker.MAX_DELIVERY_ATTEMPTS
    if not exhausted:
        assert asyncio.run(worker.drain_one(event_id))
    row = persisted()
    assert row.state == ("failed" if exhausted else "delivered")
    assert row.attempt_count == len(failures) + (0 if exhausted else 1)
    assert row.payload_json == frozen
    assert row.processed_at is not None
    assert not asyncio.run(worker.drain_one(event_id))
    assert len(receiver.requests) == row.attempt_count
    bodies = [body for body, _headers in receiver.requests]
    assert all(body == bodies[0] for body in bodies)
    payload = json.loads(bodies[0])
    assert payload["id"] == str(event_id)
    assert payload["type"] == EVENT
    assert payload["data"]["title"] == frozen["title"]
    assert payload["data"]["labels"] == frozen["labels"]
    assert payload["data"]["assignees"] == []
    assert payload["data"]["source_revision"] == frozen["source_revision"]
    assert payload["data"]["execution_generation"] == 3
    for body, headers in receiver.requests:
        assert headers["X-Langboard-Webhook-Id"] == str(event_id)
        verify_webhook(body, headers, SECRET)

    # Send altered bytes through the same real socket with the captured signature.
    body, headers = receiver.requests[-1]
    replay_headers = {name: value for name, value in headers.items() if name.lower() != "content-length"}
    with httpx.Client(timeout=2, trust_env=False) as client:
        response = client.post(receiver.url, content=body + b" ", headers=replay_headers)
    assert response.status_code == 401
    assert receiver.rejected == 1
    assert len(receiver.requests) == row.attempt_count
    with engine.connect() as connection:
        count = connection.execute(text("SELECT total_used_count FROM webhook_setting WHERE id = 40")).scalar_one()
    assert count == (0 if exhausted else 1)
