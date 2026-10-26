"""Replay actual Alembic history and preserve populated canonical app evidence."""

import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AppConnection,
    AppDefinition,
    AppExecutionAcknowledgment,
    AppExecutionActive,
    AppExecutionLease,
    AppExecutionOutbox,
    AppExecutionRequest,
    AppExecutionReview,
    AppExecutionStart,
    AppMcpConsent,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppOwnershipAudit,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
    CardExecutionIntent,
    Project,
    ProjectColumn,
    User,
    UserAppPreference,
)
from sqlalchemy import create_engine, insert, inspect, text


def save(db, obj):
    columns = {c["name"] for c in inspect(db).get_columns(obj.__table__.name)}
    # Historical fixture must use only columns actually present at that revision.
    db.execute(
        insert(obj.__table__).values(
            **{c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name in columns}
        )
    )


def snapshot(db, tables):
    return {
        name: db.execute(text(f'SELECT to_jsonb(t) FROM "{name}" t ORDER BY id')).scalars().all() for name in tables
    }


@pytest.fixture
def migration_engine():
    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL migration replay requires LANGBOARD_FILE_TEST_DATABASE_URL")
    engine = create_engine(url)
    # Extensions are database scoped; keep them outside disposable schemas.
    with engine.begin() as db:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"))
    token = uuid4().hex
    schemas = [f"union_populated_{token}", f"union_fresh_{token}"]
    try:
        yield engine, schemas
    finally:
        try:
            for schema in schemas:
                # Release each schema's DDL locks before dropping the next one.
                with engine.begin() as db:
                    db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            engine.dispose()


def test_fresh_union_and_populated_canary_history_preserve_evidence(migration_engine):
    engine, (populated_schema, fresh_schema) = migration_engine
    with engine.connect() as db:
        extension = db.execute(
            text(
                "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm'"
            )
        ).scalar()
        extension_schema = engine.dialect.identifier_preparer.quote(extension) if extension else "public"
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "langboard/migrations"))
    current_head = ScriptDirectory.from_config(config).get_current_head()
    assert current_head is not None
    with engine.begin() as db:
        db.execute(text(f"CREATE SCHEMA {populated_schema}"))
    with engine.connect() as db:
        db.execute(text(f"SET search_path TO {populated_schema}, public, {extension_schema}"))
        db.commit()
        config.attributes["connection"] = db
        command.upgrade(config, "c97659de63f4")
        now = SafeDateTime.now()
        fixtures = [
            User(
                id=101,
                firstname="Test",
                lastname="Human",
                email="migration@example.invalid",
                password="fixture",
                activated_at=now,
            ),
            Project(id=102, owner_id=101, title="Retained Board"),
            ProjectColumn(id=103, project_id=102, name="Ready", workflow_stage="ready"),
            Card(id=104, project_id=102, project_column_id=103, title="Retained task", last_change_seq=41),
            AppDefinition(
                id=105,
                key="example-app",
                declaration={"schema_version": 1, "key": "example-app"},
                approved_by=101,
                package={"digest": "retained-package"},
            ),
            AppConnection(id=106, app_key="example-app", owner_id=101, state="connected"),
            BoardAppBinding(
                id=107,
                project_id=102,
                app_key="example-app",
                connection_id=106,
                state="enabled",
                granted_capabilities=["resources.read"],
                workflow_mapping={"ready": "retained"},
            ),
            AppResourceBinding(
                id=108,
                board_binding_id=107,
                connection_id=106,
                resource_type="project",
                external_resource_id="retained-project",
                is_selected=True,
                access_state="granted",
                access_revision=7,
            ),
            CardAppOwnership(id=109, card_id=104, app_key="example-app", revision=3),
            CardAppOwnershipAudit(
                id=110, card_id=104, actor_id=101, previous_app_key=None, app_key="example-app", revision=3
            ),
            CardAppResourceSelection(
                id=111,
                card_id=104,
                app_key="example-app",
                connection_id=106,
                resource_uids=["resource-evidence"],
                revision=4,
            ),
            CardAppResourceSelectionAudit(
                id=112,
                card_id=104,
                actor_id=101,
                app_key="example-app",
                connection_id=106,
                resource_uids=["resource-evidence"],
                revision=4,
            ),
            AppExecutionRequest(
                id=113,
                project_id=102,
                card_id=104,
                app_key="example-app",
                connection_id=106,
                generation=8,
                authority={"authority_version": "accepted-authority"},
            ),
            AppExecutionOutbox(
                id=114,
                request_id=113,
                project_id=102,
                app_key="example-app",
                connection_id=106,
                event_type="io.langboard.app.execution.requested.v1",
                attempt_count=3,
                state="delivered",
                delivery_history=[{"state": "failed"}, {"state": "delivered"}],
            ),
            AppExecutionOutbox(
                id=115,
                request_id=113,
                project_id=102,
                app_key="example-app",
                connection_id=106,
                event_type="io.langboard.app.interaction.answered.v1",
                attempt_count=2,
                state="failed",
                delivery_history=[{"reason": "timeout"}],
            ),
            AppExecutionAcknowledgment(
                id=116,
                request_id=113,
                event_id=114,
                app_key="example-app",
                connection_id=106,
                runtime_reference="retained-runtime",
            ),
            AppExecutionLease(
                id=117,
                request_id=113,
                acknowledgment_id=116,
                runtime_token_hash="retained-hash",
                state="stopped",
                expires_at=now + timedelta(minutes=1),
                history=[{"state": "authorized"}, {"state": "stopped"}],
            ),
            AppExecutionStart(
                id=118,
                lease_id=117,
                request_id=113,
                acknowledgment_id=116,
                generation=8,
                runtime_reference="retained-runtime",
                execution_reference="retained-process",
            ),
            AppExecutionReview(
                id=119,
                request_id=113,
                fingerprint="retained-fingerprint",
                receipt={"evidence_reference": "retained-result"},
            ),
            AppMcpConsent(
                id=120,
                connection_id=106,
                identity_hash="retained-identity",
                scope_tools={"read": ["cards.read"]},
                revision=5,
            ),
        ]
        for obj in fixtures:
            save(db, obj)
        db.execute(text("INSERT INTO card_execution_generation VALUES(104,8)"))
        db.commit()
        tables = [obj.__table__.name for obj in fixtures]
        tables = list(dict.fromkeys(tables))
        before = snapshot(db, tables)
        command.upgrade(config, "9bcd012e3456")
        after = snapshot(db, tables)
        # Validate every additive value before comparing all retained columns.
        for row in after["card"]:
            assert row.pop("change_count_since_seq") == row["last_change_seq"], "Card change-count backfill drifted"
        additive_defaults = {
            "board_app_binding": {"resource_selection_revision": 0},
            "app_resource_binding": {"identity_hash": ""},
            "user": {"previous_avatar": None, "avatar_revision": 0},
            "project": {"avatar": None, "previous_avatar": None, "avatar_revision": 0},
            "card": {"workflow_stage_entered_key": None, "workflow_stage_entered_at": None},
        }
        for table, defaults in additive_defaults.items():
            for row in after[table]:
                for column, expected in defaults.items():
                    assert row.pop(column) == expected, f"Unexpected additive default: {table}.{column}"
        assert after == before, "Retained app evidence changed"
        assert (
            db.execute(text("SELECT execution_generation FROM card_execution_generation WHERE card_id=104")).scalar()
            == 8
        )
        db.commit()
        command.downgrade(config, "d0876afe1402")
        command.upgrade(config, "9bcd012e3456")
        save(
            db,
            CardExecutionIntent(
                id=121,
                card_id=104,
                actor_id=101,
                idempotency_key="retained-intent-key",
                execution_generation=9,
                app_key="example-app",
                connection_id=106,
                snapshot={"observed": "retained"},
            ),
        )
        save(
            db,
            AppExecutionActive(
                id=122,
                lease_id=117,
                start_id=118,
                request_id=113,
                project_id=102,
                card_id=104,
                generation=8,
                column_id=103,
                snapshot={"observed": "retained"},
                inputs={"revision": 1},
            ),
        )
        db.commit()
        try:
            command.downgrade(config, "d0876afe1402")
        except RuntimeError as exc:
            assert "Cannot discard" in str(exc)
            db.rollback()
        else:
            raise AssertionError("Populated downgrade erased audit history")
        assert db.execute(text("SELECT count(*) FROM card_execution_intent")).scalar() == 1
        assert db.execute(text("SELECT count(*) FROM app_execution_active")).scalar() == 1
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == "9bcd012e3456"
        save(
            db,
            UserAppPreference(id=123, user_id=101, revision=7, pinned_apps=[{"key": "example-app", "name": "Example"}]),
        )
        db.commit()
        try:
            command.downgrade(config, "e1987bdf2503")
        except RuntimeError as exc:
            assert "Cannot discard saved app rail preferences" in str(exc)
            db.rollback()
        else:
            raise AssertionError("Populated downgrade erased app preferences")
        assert db.execute(text("SELECT revision, pinned_apps FROM user_app_preference")).first() == (
            7,
            [{"key": "example-app", "name": "Example"}],
        )
        print("POPULATED_HISTORY_PRESERVED; EMPTY_ROUNDTRIP_OK; POPULATED_DOWNGRADE_REFUSED")
        # The retirement is deliberately irreversible. Historical downgrade guards
        # above must still execute at the last reversible revision.
        retained = snapshot(db, tables)
        preferences = db.execute(text("SELECT revision, pinned_apps FROM user_app_preference")).all()
        db.commit()
        command.upgrade(config, "head")
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == current_head
        db.commit()
        # The newest irreversible retirement must refuse before an older
        # workflow retirement can be reached during a head downgrade.
        with pytest.raises(RuntimeError, match="Deletion trigger retirement is irreversible"):
            command.downgrade(config, "9bcd012e3456")
        db.rollback()
        assert snapshot(db, tables) == retained
        assert db.execute(text("SELECT revision, pinned_apps FROM user_app_preference")).all() == preferences
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == current_head
        assert not {"entry_effects", "entry_bots", "policy_bots", "continuous_effects"} & {
            item["name"] for item in inspect(db).get_columns("workflow_stage_definition")
        }
    with engine.begin() as db:
        db.execute(text(f"CREATE SCHEMA {fresh_schema}"))
    with engine.connect() as db:
        db.execute(text(f"SET search_path TO {fresh_schema}, public, {extension_schema}"))
        db.commit()
        config.attributes["connection"] = db
        command.upgrade(config, "head")
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == current_head
        inspector = inspect(db)
        assert inspector.get_pk_constraint("card_execution_generation")["constrained_columns"] == ["card_id"]
        assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("card_execution_intent")} == {
            ("card_id", "idempotency_key"),
            ("card_id", "execution_generation"),
        }
        assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("app_execution_active")} == {
            ("lease_id",)
        }
        for table, name in (
            ("card_execution_intent", "ck_card_execution_intent_positive"),
            ("app_execution_active", "ck_app_execution_active_positive"),
        ):
            assert name in {item["name"] for item in inspector.get_check_constraints(table)}
        # ORM reads must use the actual migrated tables, not model-created fixtures.
        from langboard_shared.domain.models.CardExecutionGeneration import CardExecutionGeneration
        from sqlalchemy.orm import Session

        with Session(bind=db) as session:
            assert session.query(CardExecutionGeneration).all() == []
            assert session.query(CardExecutionIntent).all() == []
            assert session.query(AppExecutionActive).all() == []
        print("FRESH_UPSTREAM_CANARY_HEAD_REPLAY_OK; CANONICAL_KEYS_CONSTRAINTS_AND_ORM_READS_OK")
