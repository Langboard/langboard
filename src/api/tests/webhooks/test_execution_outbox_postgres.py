"""Real PostgreSQL proofs for the execution outbox install and delivery."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


os.environ.setdefault("PROJECT_NAME", "langboard")
from langboard_shared.core.db.Models import BaseDbModel


DATABASE_URL = os.getenv("LANGBOARD_OUTBOX_TEST_DATABASE_URL")
MIGRATION = (
    Path(__file__).parents[2]
    / "langboard/migrations/versions/20260924220000-7ad15b1d0b70.py"
)
RECONCILIATION = Path(__file__).parents[4] / "scripts/reconcile_execution_squash.sql"


@pytest.mark.skipif(not DATABASE_URL, reason="dedicated PostgreSQL proof URL not set")
def test_fresh_install_creates_final_schema_without_transient_objects() -> None:
    """Prove one migration step lands the final schema with zero trigger-engine residue.

    The squashed install must never create the readiness trigger mini-engine
    that the interim branch history created and dropped again, so a fresh
    install and the upgrade path converge on the same schema.
    """
    spec = importlib.util.spec_from_file_location("execution_outbox_install_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"outbox_install_{uuid4().hex}"
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            # Baseline that the public main history already provides.
            connection.execute(text(
                "CREATE TABLE card (id bigint PRIMARY KEY, project_id bigint, title text, updated_at timestamptz)"
            ))
            connection.execute(text(
                "CREATE TABLE project_execution_binding (id bigint PRIMARY KEY, project_id bigint, "
                "updated_at timestamptz NOT NULL, is_enabled boolean NOT NULL, "
                "column_semantics jsonb NOT NULL, prerequisite_relationship_type_uid text, "
                "webhook_uid text, events jsonb NOT NULL)"
            ))
            with Operations.context(MigrationContext.configure(connection, opts={"target_metadata": BaseDbModel.metadata})):
                migration.upgrade()
            outbox_columns = {
                row[0]
                for row in connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'execution_outbox'"
                ), {"schema": schema})
            }
            assert outbox_columns == {
                "id", "project_id", "card_id", "execution_generation", "occurred_at",
                "event_type", "payload_json", "state", "last_error", "processed_at",
                "attempt_count", "lease_until",
            }
            for final_table in (
                "card_execution_generation",
                "execution_receipt",
                "execution_checklist_projection",
            ):
                assert connection.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = :table"
                ), {"schema": schema, "table": final_table}).scalar_one() == 1
            binding_columns = {
                row[0]
                for row in connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'project_execution_binding'"
                ), {"schema": schema})
            }
            assert {"column_semantic_ids", "prerequisite_relationship_type_id", "webhook_id"} <= binding_columns
            # Fresh installs must never see the interim trigger mini-engine.
            assert connection.execute(text(
                "SELECT count(*) FROM information_schema.triggers WHERE trigger_schema = :schema"
            ), {"schema": schema}).scalar_one() == 0
            assert connection.execute(text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
                "WHERE n.nspname = :schema AND p.proname LIKE 'execution%'"
            ), {"schema": schema}).scalar_one() == 0
            # The install step is reversible for databases that only ever ran it.
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            for dropped_table in (
                "execution_outbox",
                "card_execution_generation",
                "execution_receipt",
                "execution_checklist_projection",
            ):
                assert connection.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = :table"
                ), {"schema": schema, "table": dropped_table}).scalar_one() == 0
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="dedicated PostgreSQL proof URL not set")
def test_legacy_execution_squash_reconciliation_preserves_rows_and_matches_fresh_names() -> None:
    """A deployed legacy canary can adopt the squashed history without losing work."""
    import psycopg

    spec = importlib.util.spec_from_file_location("execution_reconcile_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"outbox_reconcile_{uuid4().hex}"
    admin = create_engine(DATABASE_URL)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE card (id bigint PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE project_execution_binding (id bigint PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('b3d5e7f9a1c2')"))
            with Operations.context(MigrationContext.configure(connection, opts={"target_metadata": BaseDbModel.metadata})):
                migration.upgrade()
            connection.execute(text("ALTER TABLE card_execution_generation "
                                    "RENAME CONSTRAINT pk_card_execution_generation TO pk_card_execution_readiness"))
            connection.execute(text("ALTER TABLE card_execution_generation "
                                    "RENAME CONSTRAINT fk_card_execution_generation_card_id_card "
                                    "TO fk_card_execution_readiness_card_id_card"))
            connection.execute(text("INSERT INTO card VALUES (101)"))
            connection.execute(text("INSERT INTO card_execution_generation VALUES (101, 4)"))
            connection.execute(text("INSERT INTO execution_outbox "
                                    "(project_id, card_id, execution_generation, state) "
                                    "VALUES (7, 101, 4, 'delivered')"))
        # The script owns its transaction, just as psql does during the canary rollout.
        dsn = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(dsn, autocommit=True, options=f"-csearch_path={schema}") as connection:
            connection.execute(RECONCILIATION.read_text())
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "7ad15b1d0b70"
            assert connection.execute("SELECT execution_generation FROM card_execution_generation "
                                      "WHERE card_id = 101").fetchone()[0] == 4
            assert connection.execute("SELECT state FROM execution_outbox WHERE card_id = 101").fetchone()[0] == "delivered"
            constraints = {row[0] for row in connection.execute(
                # PostgreSQL 18 also exposes column NOT NULL constraints here.
                "SELECT conname FROM pg_constraint WHERE conrelid = 'card_execution_generation'::regclass "
                "AND contype <> 'n'"
            )}
            assert constraints == {"pk_card_execution_generation", "fk_card_execution_generation_card_id_card"}
            with pytest.raises(psycopg.errors.RaiseException):
                connection.execute(RECONCILIATION.read_text())
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="dedicated PostgreSQL proof URL not set")
def test_delivery_lifecycle_reaches_terminal_states_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove the converged one-task delivery: delivered, failed, and crash recovery."""
    from asyncio import run as asyncio_run
    from types import SimpleNamespace
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.tasks.webhooks import ExecutionOutboxWorker as worker

    schema = f"outbox_lifecycle_{uuid4().hex}"
    admin = create_engine(DATABASE_URL)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    # This fixture persists explicit UTC binding revisions, matching application snapshots.
    engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema} -ctimezone=UTC"})
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)

    outcomes: list[Exception | None] = []
    posted_webhooks: list[str] = []

    async def fake_post(model, webhook_uid):
        posted_webhooks.append(webhook_uid)
        if outcomes:
            failure = outcomes.pop(0)
            if failure is not None:
                raise failure

    monkeypatch.setattr(worker, "post_signed_webhook", fake_post)
    monkeypatch.setattr(worker, "binding_invalid_reasons", lambda binding, event: [])
    try:
        with engine.begin() as connection:
            for statement in (
                "CREATE TABLE project (id bigint PRIMARY KEY, deleted_at timestamptz)",
                "INSERT INTO project VALUES (7, NULL)",
                "CREATE TABLE project_column (id bigint PRIMARY KEY, deleted_at timestamptz, is_archive boolean NOT NULL, workflow_stage text, project_id bigint)",
                "CREATE TABLE global_card_relationship_type (id bigint PRIMARY KEY, machine_semantic text, is_active boolean)",
                "CREATE TABLE webhook_setting (id bigint PRIMARY KEY, secret_id bigint, events jsonb NOT NULL)",
                "CREATE TABLE project_execution_binding (id bigint PRIMARY KEY, project_id bigint UNIQUE, "
                "updated_at timestamptz NOT NULL, is_enabled boolean NOT NULL, "
                "prerequisite_relationship_type_id bigint, webhook_id bigint, webhook_uid text, events jsonb NOT NULL, "
                "column_semantic_ids jsonb NOT NULL)",
                "CREATE TABLE card (id bigint PRIMARY KEY, project_id bigint, project_column_id bigint, "
                "deleted_at timestamptz, archived_at timestamptz, source_type text, title text, updated_at timestamptz)",
                "CREATE TABLE card_relationship (card_id_parent bigint, card_id_child bigint, relationship_type_id bigint)",
                "CREATE TABLE project_label (id bigint PRIMARY KEY, name text)",
                "CREATE TABLE card_assigned_project_label (card_id bigint, project_label_id bigint)",
                "CREATE TABLE card_assigned_user (card_id bigint, user_id bigint)",
                "CREATE TABLE card_execution_generation (card_id bigint PRIMARY KEY, execution_generation integer "
                "NOT NULL DEFAULT 0)",
                "CREATE TABLE execution_outbox (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id bigint, "
                "card_id bigint, execution_generation integer, occurred_at timestamptz DEFAULT now(), event_type text, "
                "payload_json jsonb, state text DEFAULT 'pending', last_error text, processed_at timestamptz, "
                "attempt_count integer NOT NULL DEFAULT 0, lease_until timestamptz, "
                "CONSTRAINT uq_execution_outbox_card_generation UNIQUE(card_id, execution_generation))",
            ):
                connection.execute(text(statement))
            from langboard_shared.domain.models import WorkflowStageDefinition

            WorkflowStageDefinition.__table__.create(connection)
            connection.execute(text("INSERT INTO project_column VALUES (20, NULL, false, NULL, 7)"))
            connection.execute(text("INSERT INTO global_card_relationship_type VALUES (30, 'blocks', true)"))
            connection.execute(
                text("INSERT INTO webhook_setting VALUES (40, 50, CAST(:events AS jsonb))"),
                {"events": '["io.langboard.work.ready.v1"]'},
            )
            connection.execute(
                text(
                    "INSERT INTO project_execution_binding VALUES "
                    "(8, 7, '2026-09-24T00:00:00+00:00', true, 30, 40, 'hook-40', "
                    "CAST(:events AS jsonb), CAST(:semantics AS jsonb))"
                ),
                {"events": '["io.langboard.work.ready.v1"]', "semantics": '{"20":"ready"}'},
            )
            connection.execute(
                text(
                    "INSERT INTO card VALUES "
                    "(100, 7, 20, NULL, NULL, NULL, 'Task', '2026-09-24T00:00:00+00:00')"
                )
            )
            connection.execute(
                text("INSERT INTO card_execution_generation VALUES (100, 3)")
            )
            connection.execute(
                text(
                    "INSERT INTO execution_outbox(project_id, card_id, execution_generation, event_type, payload_json) "
                    "VALUES (7, 100, 3, 'io.langboard.work.ready.v1', CAST(:payload AS jsonb))"
                ),
                {
                    "payload": (
                        '{"title": "Task", "labels": [], "assignee_ids": [], '
                        '"source_revision": "2026-09-24T00:00:00+00:00", "binding_id": "8", '
                        '"binding_revision": "2026-09-24T00:00:00+00:00", "webhook_uid": "hook-40"}'
                    )
                },
            )
        def read_current_binding(uid):
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT id, updated_at, webhook_uid FROM project_execution_binding WHERE project_id = 7")
                ).one()
            return SimpleNamespace(id=row[0], updated_at=row[1], webhook_uid=row[2])

        monkeypatch.setattr(worker, "binding_for_project", read_current_binding)

        # Completion 1: success reaches the terminal delivered state.
        assert asyncio_run(worker.drain_one())
        with engine.connect() as connection:
            state, attempts, lease = connection.execute(
                text("SELECT state, attempt_count, lease_until FROM execution_outbox WHERE card_id = 100")
            ).one()
        assert state == "delivered"
        assert attempts == 1
        assert lease is not None

        # Completion 2: HTTP failures retry with a bounded attempt budget, then fail terminally.
        outcomes.extend([RuntimeError("500")] * worker.MAX_DELIVERY_ATTEMPTS)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE execution_outbox SET state = 'pending', attempt_count = 0, lease_until = NULL")
            )
        for attempt in range(1, worker.MAX_DELIVERY_ATTEMPTS + 1):
            with pytest.raises(worker.ExecutionDeliveryFailed):
                asyncio_run(worker.drain_one())
            with engine.connect() as connection:
                state, attempts, lease = connection.execute(
                    text("SELECT state, attempt_count, lease_until FROM execution_outbox WHERE card_id = 100")
                ).one()
            assert attempts == attempt
            assert state == ("failed" if attempt == worker.MAX_DELIVERY_ATTEMPTS else "pending")
            assert lease is None
        assert not asyncio_run(worker.drain_one())
        with engine.connect() as connection:
            state, error, attempts = connection.execute(
                text("SELECT state, last_error, attempt_count FROM execution_outbox WHERE card_id = 100")
            ).one()
        assert state == "failed"
        assert error == "delivery_attempts_exhausted"
        assert attempts == worker.MAX_DELIVERY_ATTEMPTS

        # Completion 3: a worker crash between claim and HTTP leaves a stale lease that recovery re-drains.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE execution_outbox SET state = 'pending', last_error = NULL, attempt_count = 0, "
                    "lease_until = NULL"
                )
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE execution_outbox SET state = 'delivering', attempt_count = 1, "
                    "lease_until = now() - interval '1 second'"
                )
            )
        assert asyncio_run(worker.drain_one())
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT state FROM execution_outbox WHERE card_id = 100")).scalar_one()
                == "delivered"
            )

        # A live lease is not re-claimed, bounding duplicate concurrent delivery.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE execution_outbox SET state = 'delivering', attempt_count = 1, "
                    "lease_until = now() + interval '1 minute', last_error = NULL"
                )
            )
        assert not asyncio_run(worker.drain_one())
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT state FROM execution_outbox WHERE card_id = 100")).scalar_one()
                == "delivering"
            )

        # Recovery skips a row locked by another claimant without waiting.
        with engine.begin() as connection:
            connection.execute(text("UPDATE execution_outbox SET state = 'pending', attempt_count = 0, lease_until = NULL"))
        with engine.begin() as claimant:
            event_id = claimant.execute(text("SELECT id FROM execution_outbox FOR UPDATE")).scalar_one()
            assert not asyncio_run(worker.drain_one())

        # The event-specific hot path and recovery share one live claim while HTTP is in flight.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        entered, release = Event(), Event()
        async def waiting_post(model, webhook_uid):
            entered.set()
            assert release.wait(5), "test delivery was not released"
            await fake_post(model, webhook_uid)

        monkeypatch.setattr(worker, "post_signed_webhook", waiting_post)
        posts_before = len(posted_webhooks)
        with ThreadPoolExecutor(max_workers=1) as executor:
            hot_path = executor.submit(lambda: asyncio_run(worker.drain_one(event_id)))
            try:
                assert entered.wait(5), "hot path did not reach delivery"
                assert not asyncio_run(worker.drain_one())
            finally:
                release.set()
            assert hot_path.result(timeout=5)
        monkeypatch.setattr(worker, "post_signed_webhook", fake_post)
        assert len(posted_webhooks) == posts_before + 1
        with engine.connect() as connection:
            assert connection.execute(text("SELECT state, attempt_count FROM execution_outbox")).one() == ("delivered", 1)

        # A slow response from an expired claim must not overwrite its replacement.
        for old_failure in (False, True):
            for replacement_delivered in (None, False, True):
                with engine.begin() as connection:
                    connection.execute(text(
                        "UPDATE execution_outbox SET state = 'pending', attempt_count = 0, "
                        "lease_until = NULL, last_error = NULL, processed_at = NULL"
                    ))
                entered, release = Event(), Event()

                async def stale_post(model, webhook_uid):
                    entered.set()
                    assert release.wait(5), "stale delivery was not released"
                    if old_failure:
                        raise RuntimeError("late HTTP failure")

                monkeypatch.setattr(worker, "post_signed_webhook", stale_post)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    stale = executor.submit(lambda: asyncio_run(worker.drain_one(event_id)))
                    try:
                        assert entered.wait(5), "stale delivery did not reach HTTP"
                        with engine.begin() as connection:
                            connection.execute(text(
                                "UPDATE execution_outbox SET lease_until = now() - interval '1 second'"
                            ))
                        monkeypatch.setattr(worker, "post_signed_webhook", fake_post)
                        if replacement_delivered:
                            assert asyncio_run(worker.drain_one(event_id))
                        elif replacement_delivered is False:
                            assert isinstance(worker._claim(event_id), worker.ClaimedDelivery)
                        with engine.connect() as connection:
                            replacement = connection.execute(text(
                                "SELECT state, attempt_count, lease_until, last_error, processed_at "
                                "FROM execution_outbox"
                            )).one()
                        assert replacement[0] == ("delivered" if replacement_delivered else "delivering")
                        assert replacement[1] == (1 if replacement_delivered is None else 2)
                    finally:
                        release.set()
                    if old_failure:
                        with pytest.raises(worker.ExecutionDeliveryFailed):
                            stale.result(timeout=5)
                    else:
                        assert stale.result(timeout=5)
                with engine.connect() as connection:
                    assert connection.execute(text(
                        "SELECT state, attempt_count, lease_until, last_error, processed_at FROM execution_outbox"
                    )).one() == replacement

        # Operator recovery re-queues terminal failures with a fresh attempt budget.
        with engine.begin() as connection:
            connection.execute(text("UPDATE execution_outbox SET state = 'failed', last_error = 'delivery_attempts_exhausted'"))
        assert worker.reconcile(_short_code_for(7)) == 1
        with engine.connect() as connection:
            state, attempts = connection.execute(
                text("SELECT state, attempt_count FROM execution_outbox WHERE card_id = 100")
            ).one()
        assert state == "pending"
        assert attempts == 0

        # Changing a committed destination must persist a block, never POST to either target.
        for assignment in (
            "id = 9",
            "updated_at = '2026-09-24T01:00:00+00:00'",
            "webhook_uid = 'hook-41'",
        ):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE project_execution_binding SET id = 8, "
                    "updated_at = '2026-09-24T00:00:00+00:00', webhook_uid = 'hook-40'"
                ))
                connection.execute(text(f"UPDATE project_execution_binding SET {assignment}"))
                connection.execute(text(
                    "UPDATE execution_outbox SET state = 'pending', last_error = NULL, "
                    "attempt_count = 0, lease_until = NULL"
                ))
                frozen_payload = connection.execute(text("SELECT payload_json FROM execution_outbox")).scalar_one()
            posts_before = len(posted_webhooks)
            assert asyncio_run(worker.drain_one())
            with engine.connect() as connection:
                state, error, attempts, payload = connection.execute(text(
                    "SELECT state, last_error, attempt_count, payload_json FROM execution_outbox"
                )).one()
            assert (state, error, attempts) == ("blocked", "stale_binding", 0)
            assert payload == frozen_payload
            assert len(posted_webhooks) == posts_before
            assert not asyncio_run(worker.drain_one())

        # A failed destination cannot stop cron recovery of later committed events.
        # Each event gets at most one HTTP attempt in this recovery invocation.
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE project_execution_binding SET id = 8, "
                "updated_at = '2026-09-24T00:00:00+00:00', webhook_uid = 'hook-40'"
            ))
            connection.execute(text(
                "UPDATE execution_outbox SET state = 'pending', last_error = NULL, "
                "attempt_count = 0, lease_until = NULL, occurred_at = now() - interval '1 minute'"
            ))
            connection.execute(text(
                "INSERT INTO card SELECT 101, project_id, project_column_id, deleted_at, "
                "archived_at, source_type, title, updated_at FROM card WHERE id = 100"
            ))
            connection.execute(text("INSERT INTO card_execution_generation VALUES (101, 3)"))
            connection.execute(text(
                "INSERT INTO execution_outbox(project_id, card_id, execution_generation, event_type, payload_json) "
                "SELECT project_id, 101, execution_generation, event_type, payload_json FROM execution_outbox"
            ))
        outcomes.append(RuntimeError("first destination unavailable"))
        posts_before = len(posted_webhooks)
        assert asyncio_run(worker.drain_pending()) == 1
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT card_id, state, attempt_count FROM execution_outbox ORDER BY card_id"
            )).all() == [(100, "pending", 1), (101, "delivered", 1)]
        assert len(posted_webhooks) == posts_before + 2
        assert asyncio_run(worker.drain_pending()) == 1
        assert not asyncio_run(worker.drain_pending())
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT card_id, state, attempt_count FROM execution_outbox ORDER BY card_id"
            )).all() == [(100, "delivered", 2), (101, "delivered", 1)]

        # Tied timestamps span multiple bounded pages. Failures in earlier pages
        # must neither repeat nor prevent delivery beyond the first page.
        from uuid import UUID
        from sqlalchemy import event

        monkeypatch.setattr(worker, "RECOVERY_BATCH_SIZE", 2, raising=False)

        def insert_recovery_event(card_id, occurred_at):
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO card SELECT :card_id, project_id, project_column_id, deleted_at, "
                    "archived_at, source_type, title, updated_at FROM card WHERE id = 100"
                ), {"card_id": card_id})
                connection.execute(text("INSERT INTO card_execution_generation VALUES (:card_id, 3)"),
                                   {"card_id": card_id})
                connection.execute(text(
                    "INSERT INTO execution_outbox(id, project_id, card_id, execution_generation, "
                    "occurred_at, event_type, payload_json) SELECT :id, project_id, :card_id, "
                    "execution_generation, CAST(:occurred_at AS timestamptz), event_type, payload_json "
                    "FROM execution_outbox WHERE card_id = 100"
                ), {"id": UUID(int=card_id), "card_id": card_id, "occurred_at": occurred_at})

        for card_id in range(102, 107):
            insert_recovery_event(card_id, "2026-09-24T00:00:00+00:00")
        recovery_queries = []

        def capture_recovery_query(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT execution_outbox.") and "FOR UPDATE" not in statement:
                recovery_queries.append(statement)

        attempted = []

        async def mixed_post(model, webhook_uid):
            attempted.append(model.event_id)
            if len(attempted) == 1:
                # A new event belongs to the next invocation, keeping this pass finite.
                insert_recovery_event(107, "2026-09-25T00:00:00+00:00")
            if model.event_id in {str(UUID(int=card_id)) for card_id in range(102, 105)}:
                raise RuntimeError("destination unavailable")

        monkeypatch.setattr(worker, "post_signed_webhook", mixed_post)
        event.listen(engine, "before_cursor_execute", capture_recovery_query)
        try:
            assert asyncio_run(worker.drain_pending()) == 2
        finally:
            event.remove(engine, "before_cursor_execute", capture_recovery_query)
        assert len(recovery_queries) >= 4 and all("LIMIT" in query for query in recovery_queries)
        assert attempted == [str(UUID(int=card_id)) for card_id in range(102, 107)]
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT card_id, state, attempt_count FROM execution_outbox WHERE card_id >= 102 ORDER BY card_id"
            )).all() == [
                (102, "pending", 1), (103, "pending", 1), (104, "pending", 1),
                (105, "delivered", 1), (106, "delivered", 1), (107, "pending", 0),
            ]
        monkeypatch.setattr(worker, "post_signed_webhook", fake_post)
        assert asyncio_run(worker.drain_pending()) == 4
        assert not asyncio_run(worker.drain_pending())
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


def _short_code_for(project_id: int) -> str:
    """Convert an internal project id to its public short code for reconcile."""
    from langboard_shared.core.types import SnowflakeID

    return SnowflakeID(project_id).to_short_code()


@pytest.mark.skipif(not DATABASE_URL, reason="dedicated PostgreSQL proof URL not set")
def test_producer_snapshot_survives_retry_and_session_restart_with_current_fences(monkeypatch) -> None:
    """Native producer and policy preserve event data across edits, retry and crash recovery."""
    from asyncio import run as asyncio_run
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import (
        GlobalCardRelationshipType,
        Project,
        ProjectColumn,
        ProjectExecutionBinding,
        WebhookSetting,
        WorkflowStageDefinition,
    )
    from langboard_shared.tasks.webhooks import ExecutionBindingPolicy, ExecutionOutboxTask
    from langboard_shared.tasks.webhooks import ExecutionOutboxWorker as worker
    from langboard_shared.tasks.webhooks.ExecutionReadinessUow import current_execution, execution_readiness_uow
    from sqlalchemy.schema import CreateTable

    schema = f"outbox_immutable_{uuid4().hex}"
    admin = create_engine(DATABASE_URL)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    enqueued = []
    monkeypatch.setattr(ExecutionOutboxTask, "execution_outbox_task", enqueued.append)
    # External key storage and HTTP are the only delivery boundaries replaced.
    monkeypatch.setattr(ExecutionBindingPolicy.KeyVault, "get_key", lambda _: "test-only")
    posted = []

    async def post(model, webhook_uid):
        posted.append((model.model_dump(), webhook_uid))
        if len(posted) == 1:
            raise RuntimeError("first HTTP attempt failed")

    monkeypatch.setattr(worker, "post_signed_webhook", post)
    try:
        with engine.begin() as connection:
            # Native model columns retain the real policy's full ORM reads. Foreign
            # keys to unrelated account tables are outside this isolated fixture.
            for model in (Project, ProjectColumn, GlobalCardRelationshipType, ProjectExecutionBinding,
                          WebhookSetting, WorkflowStageDefinition):
                connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
            for column_name in ("column_semantic_ids", "prerequisite_relationship_type_id", "webhook_id"):
                connection.execute(text(f"ALTER TABLE project_execution_binding DROP COLUMN {column_name}"))
            for statement in (
                "CREATE TABLE card (id bigint PRIMARY KEY, project_id bigint, project_column_id bigint, "
                "deleted_at timestamptz, archived_at timestamptz, source_type text, title text, updated_at timestamptz)",
                "CREATE TABLE card_relationship (card_id_parent bigint, card_id_child bigint, relationship_type_id bigint)",
                "CREATE TABLE project_label (id bigint PRIMARY KEY, name text)",
                "CREATE TABLE card_assigned_project_label (card_id bigint, project_label_id bigint)",
                "CREATE TABLE card_assigned_user (card_id bigint, user_id bigint)",
            ):
                connection.execute(text(statement))
            spec = importlib.util.spec_from_file_location("immutable_outbox_migration", MIGRATION)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        project = Project(id=7, owner_id=123, title="Fixture")
        ready = ProjectColumn(id=20, project_id=7, name="Ready")
        terminal = ProjectColumn(id=21, project_id=7, name="Done")
        relation = GlobalCardRelationshipType(id=30, parent_name="Blocks", child_name="Blocked by",
                                              machine_semantic="blocks")
        hook = WebhookSetting(id=40, name="Executor", url="https://executor.invalid", secret_id="test-only",
                              events=["io.langboard.work.ready.v1"])
        binding = ProjectExecutionBinding(
            id=8, project_id=7, is_enabled=True, events=["io.langboard.work.ready.v1"],
            column_semantics={ready.get_uid(): "ready", terminal.get_uid(): "terminal"},
            column_semantic_ids={"20": "ready", "21": "terminal"},
            prerequisite_relationship_type_id=30, prerequisite_relationship_type_uid=relation.get_uid(),
            webhook_id=40, webhook_uid=hook.get_uid(),
        )
        with DbSession.atomic() as db:
            for record in (project, ready, terminal, relation, hook, binding):
                db.exec(record.__table__.insert().values(record.model_dump()))
            db.exec(text("INSERT INTO card VALUES "
                         "(100, 7, 21, NULL, NULL, NULL, 'Original', '2026-09-24T00:00:00+00:00')"))
            db.exec(text("INSERT INTO project_label VALUES (9, 'backend')"))
            db.exec(text("INSERT INTO card_assigned_project_label VALUES (100, 9)"))
            db.exec(text("INSERT INTO card_assigned_user VALUES (100, 123)"))
        with execution_readiness_uow() as execution:
            execution.watch([100])
            execution.db.exec(text("UPDATE card SET project_column_id = 20 WHERE id = 100"))
        with engine.connect() as connection:
            event_id, frozen = connection.execute(text("SELECT id, payload_json FROM execution_outbox")).one()
        assert enqueued == [str(event_id)]
        assert frozen["title"] == "Original"
        assert frozen["labels"] == ["backend"]
        assert frozen["assignee_ids"] == [123]
        # Exercise the context projection's PostgreSQL JSONB/body path against the
        # real database, including independent edits that keep Card.updated_at.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE card ADD COLUMN description jsonb"))
            connection.execute(text("UPDATE card SET description = CAST(:body AS jsonb)"), {"body": '{"content": "Original body"}'})
        original_context = current_execution(100, include_description=True)
        assert original_context.description == "Original body"
        assert original_context.labels == ["backend"]
        assert original_context.assignee_ids == [123]
        assert original_context.is_ready and original_context.generation == 1
        with engine.begin() as connection:
            connection.execute(text("UPDATE project_label SET name = 'renamed independently'"))
            connection.execute(text("UPDATE card SET description = CAST(:body AS jsonb)"), {"body": '{"content": "Updated body 한글"}'})
        updated_context = current_execution(100, include_description=True)
        assert updated_context.revision == original_context.revision
        assert updated_context.generation == original_context.generation
        assert updated_context.is_ready
        assert updated_context.labels == ["renamed independently"]
        assert updated_context.description == "Updated body 한글"
        assert current_execution(100).description is None
        with engine.begin() as connection:
            connection.execute(text("UPDATE project_label SET name = 'backend'"))
        with pytest.raises(worker.ExecutionDeliveryFailed):
            asyncio_run(worker.drain_one(event_id))
        with engine.connect() as connection:
            assert connection.execute(text("SELECT state, attempt_count FROM execution_outbox")).one() == ("pending", 1)
        with execution_readiness_uow() as execution:
            execution.watch([100])
            execution.db.exec(text("UPDATE card SET title = 'Edited', updated_at = '2026-09-25T00:00:00+00:00'"))
            execution.db.exec(text("UPDATE project_label SET name = 'reviewer'"))
            execution.db.exec(text("UPDATE card_assigned_user SET user_id = 456"))
        assert enqueued == [str(event_id)]
        assert current_execution(100).generation == 1
        assert current_execution(100).labels == ["reviewer"]
        # Discard all pooled connections. The retry reads persisted data in a new session.
        engine.dispose()
        assert asyncio_run(worker.drain_one(event_id))
        assert posted[0] == posted[1]
        assert posted[1][0]["data"]["title"] == "Original"
        assert posted[1][0]["data"]["labels"] == ["backend"]
        assert posted[1][0]["data"]["assignees"] == [_short_code_for(123)]
        assert posted[1][0]["data"]["source_revision"] == frozen["source_revision"]
        with engine.begin() as connection:
            assert connection.execute(text("SELECT payload_json FROM execution_outbox")).scalar_one() == frozen
            connection.execute(text("UPDATE execution_outbox SET state = 'pending', attempt_count = 0, lease_until = NULL"))
        crashed = worker._claim(event_id)
        assert isinstance(crashed, worker.ClaimedDelivery)
        assert crashed.model.model_dump() == posted[0][0]
        with engine.begin() as connection:
            connection.execute(text("UPDATE execution_outbox SET lease_until = now() - interval '1 second'"))
            connection.execute(text("UPDATE card SET title = 'After crash'"))
        engine.dispose()
        assert asyncio_run(worker.drain_one(event_id))
        assert posted[2] == posted[0]
        # Current policy revocation still blocks delivery with intact historical data.
        for revoke, restore, expected in (
            ("UPDATE card SET project_column_id = 21", "UPDATE card SET project_column_id = 20", "stale_readiness"),
            ("UPDATE card_execution_generation SET execution_generation = 2",
             "UPDATE card_execution_generation SET execution_generation = 1", "stale_readiness"),
            ("UPDATE project_execution_binding SET updated_at = updated_at + interval '1 second'",
             "UPDATE project_execution_binding SET updated_at = updated_at - interval '1 second'", "stale_binding"),
            ("UPDATE webhook_setting SET events = '[]'", "UPDATE webhook_setting SET events = '[\"io.langboard.work.ready.v1\"]'",
             "stale_readiness"),
        ):
            with engine.begin() as connection:
                connection.execute(text("UPDATE execution_outbox SET state = 'pending', attempt_count = 0, lease_until = NULL"))
                connection.execute(text(revoke))
            assert asyncio_run(worker.drain_one(event_id))
            with engine.begin() as connection:
                assert connection.execute(text("SELECT last_error FROM execution_outbox")).scalar_one() == expected
                assert connection.execute(text("SELECT payload_json FROM execution_outbox")).scalar_one() == frozen
                connection.execute(text(restore))
            assert len(posted) == 3
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()
