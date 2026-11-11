"""Migrated database guards reject unaudited private changes and stale evidence."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


VERSIONS = Path(__file__).parents[2] / "langboard/migrations/versions"


def revision(filename):
    spec = importlib.util.spec_from_file_location("migration_" + filename.split("-")[-1][:-3], VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NEW = "20261111071858-de2ad7f6e055.py"


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def migrated(request):
    url = request.param
    if url == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = "reviewed_visibility_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as db:
            if engine.dialect.name == "sqlite":
                db.execute(text("PRAGMA foreign_keys=ON"))
            db.execute(text('CREATE TABLE "user" (id BIGINT PRIMARY KEY)'))
            db.execute(text('CREATE TABLE bot (id BIGINT PRIMARY KEY)'))
            db.execute(text('INSERT INTO "user" VALUES (10),(20)'))
            db.execute(text("CREATE TABLE card (id BIGINT PRIMARY KEY, project_id BIGINT, "
                            "title TEXT, created_by_user_id BIGINT, created_by_bot_id BIGINT, "
                            "created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"))
            with Operations.context(MigrationContext.configure(db)):
                revision("20261007220000-1b392df0a6ce.py").upgrade()
                revision("20261007230000-3d5a71e8c942.py").upgrade()
            db.execute(text("INSERT INTO card (id,title,created_by_user_id,created_at,updated_at) "
                            "VALUES (1,'Retained',10,'2026-11-11 01:00:00.123456','2026-11-11 01:00:00.123456')"))
            db.execute(text("CREATE TABLE card_relationship (id BIGINT PRIMARY KEY, card_id_parent BIGINT, card_id_child BIGINT)"))
            migration = revision(NEW)
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
            yield db, migration
    finally:
        if schema:
            with engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def audit(db, identifier=100, source="INTERNAL", target="PRIVATE", actor=10,
          reviewed="2026-11-11 01:00:00.123456", created="2026-11-11 01:00:01.123456", channel="mcp"):
    db.execute(text("INSERT INTO card_visibility_change "
                    "(id,card_id,created_at,updated_at,changed_by_user_id,channel,previous_visibility,next_visibility,reviewed_updated_at) "
                    "VALUES (:id,1,:created,:created,:actor,:channel,:source,:target,:reviewed)"),
               dict(id=identifier, source=source, target=target, actor=actor, reviewed=reviewed, created=created, channel=channel))


def private_update(db, updated="2026-11-11 01:00:02.123456"):
    db.execute(text("UPDATE card SET visibility='PRIVATE',owner_user_id=10,updated_at=:updated WHERE id=1"),
               dict(updated=updated))


@pytest.mark.parametrize("case", ["missing", "stale", "wrong_owner", "future_audit", "old_audit", "no_review", "api", "incoming_edge", "outgoing_edge"])
def test_private_sql_requires_current_owner_review(migrated, case):
    db, _ = migrated
    with pytest.raises(IntegrityError), db.begin_nested():
        if case in {"incoming_edge", "outgoing_edge"}:
            parent, child = (2, 1) if case == "incoming_edge" else (1, 2)
            db.execute(text("INSERT INTO card_relationship VALUES(1,:parent,:child)"), dict(parent=parent, child=child))
            audit(db)
        elif case != "missing":
            kwargs = {"stale": {"reviewed": "2026-11-11 01:00:00.123455"},
                      "wrong_owner": {"actor": 20}, "future_audit": {"created": "2026-11-11 01:00:03.123456"},
                      "old_audit": {"created": "2026-11-11 01:00:00.123456"},
                      "no_review": {"reviewed": None}, "api": {"channel": "api"}}[case]
            audit(db, **kwargs)
        private_update(db)
    assert db.execute(text("SELECT title,visibility,owner_user_id FROM card WHERE id=1")).one() == ("Retained", "INTERNAL", None)
    assert db.execute(text("SELECT count(*) FROM card_visibility_change")).scalar_one() == 0


@pytest.mark.parametrize("channel", ["human_ui", "mcp"])
def test_reviewed_private_roundtrip_and_replay_rejection(migrated, channel):
    db, migration = migrated
    audit(db, channel=channel)
    private_update(db)
    db.execute(text("UPDATE card SET title='Edited privately' WHERE id=1"))
    for sql in (
        "UPDATE card SET visibility='SHARED',owner_user_id=NULL,updated_at='2026-11-11 01:00:04.123456' WHERE id=1",
        "UPDATE card SET owner_user_id=20,created_by_user_id=20 WHERE id=1",
        "UPDATE card SET created_by_bot_id=30 WHERE id=1",
    ):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(text(sql))
    audit(db, identifier=101, source="PRIVATE", target="SHARED", reviewed="2026-11-11 01:00:02.123456",
          created="2026-11-11 01:00:03.123456", channel=channel)
    db.execute(text("UPDATE card SET visibility='SHARED',owner_user_id=NULL,updated_at='2026-11-11 01:00:04.123456' WHERE id=1"))
    with pytest.raises(IntegrityError), db.begin_nested():
        private_update(db, updated="2026-11-11 01:00:05.123456")
    assert db.execute(text("SELECT title,visibility,owner_user_id FROM card WHERE id=1")).one() == ("Edited privately", "SHARED", None)
    with Operations.context(MigrationContext.configure(db)):
        with pytest.raises(RuntimeError, match="audit records"):
            migration.downgrade()


def test_downgrade_without_new_audits_restores_immutable_guard(migrated):
    db, migration = migrated
    with Operations.context(MigrationContext.configure(db)):
        migration.downgrade()
    with pytest.raises(IntegrityError), db.begin_nested():
        private_update(db)
    assert db.execute(text("SELECT title FROM card WHERE id=1")).scalar_one() == "Retained"


from src.api.tests.card_visibility.test_visibility_transition import transition_card  # noqa: E402,F401


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
def test_native_owner_and_exact_audit_work_with_database_guard(transition_card):
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card, CardRelationship, GlobalCardRelationshipType, CardVisibilityChange
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility

    user, project, card, service = transition_card
    engine = DbEngine.get_main_engine()
    for model in (GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(engine)
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            revision(NEW)._create_guard(engine.dialect.name, reviewed=True)
    for target, expected in ((CardVisibility.Private, "INTERNAL"), (CardVisibility.Shared, "PRIVATE")):
        with DbSession.use(readonly=False) as db:
            version = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().updated_at
        result = service.change_visibility(
            user, project, card, target, channel=CollaborationChannel.Mcp,
            confirmed=True, expected_visibility=expected,
            expected_updated_at=SafeDateTime.fromisoformat(version.isoformat()),
        )
        assert result["visibility"] == target.value
    with DbSession.use(readonly=False) as db:
        audits = db.exec(SqlBuilder.select.table(CardVisibilityChange)).all()
    assert len(audits) == 2 and all(a.reviewed_updated_at is not None for a in audits)



def test_postgresql_concurrent_stale_disclosure_cannot_reverse_private_commit(migrated):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    db, _ = migrated
    if db.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock concurrency case")
    db.commit()
    audit(db)
    private_update(db)
    waiting = Event()
    backend = {}

    def stale_disclosure():
        with db.engine.connect() as other:
            other.execute(text("SET lock_timeout='5s'"))
            backend["pid"] = other.execute(text("SELECT pg_backend_pid()")).scalar_one()
            audit(other, identifier=101, source="INTERNAL", target="SHARED")
            waiting.set()
            with pytest.raises(IntegrityError):
                other.execute(text("UPDATE card SET visibility='SHARED',owner_user_id=NULL, "
                                   "updated_at='2026-11-11 01:00:04.123456' WHERE id=1"))
            other.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale_disclosure)
        try:
            assert waiting.wait(timeout=3)
            deadline = monotonic() + 3
            locked = False
            while monotonic() < deadline:
                db.execute(text("SELECT pg_stat_clear_snapshot()"))
                locked = db.execute(text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"),
                                    backend).scalar_one()
                if locked:
                    break
                sleep(0.01)
            assert locked, "Competing disclosure did not reach the row lock"
        finally:
            db.commit()
        future.result(timeout=5)
    assert db.execute(text("SELECT visibility,owner_user_id FROM card WHERE id=1")).one() == ("PRIVATE", 10)
    assert db.execute(text("SELECT count(*) FROM card_visibility_change")).scalar_one() == 1
