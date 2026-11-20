"""Opt-in real Redis/Celery/PostgreSQL recovery, isolated from operational queues."""

import os
import subprocess
import sys
from pathlib import Path
import pytest


@pytest.mark.skipif(
    not os.environ.get("LANGBOARD_TEST_CLEANUP_BROKER_URL"), reason="Dedicated cleanup Redis URL required"
)
def test_real_broker_recovers_committed_cleanup_after_connection_failure(tmp_path):
    if not os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL"):
        pytest.skip("Dedicated PostgreSQL URL required")
    root = Path(__file__).resolve().parents[4]
    env = {
        **os.environ,
        "PROJECT_NAME": "langboard",
        "ENVIRONMENT": "development",
        "IS_EXECUTABLE": "false",
        "SENTRY_DSN": "",
        "PYTHONPATH": os.pathsep.join((str(root / "src/shared/py"), str(root / "src/api"))),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "scenario", str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    (tmp_path / "cleanup-broker-receipt.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REAL_BROKER_RECOVERY_OK" in result.stdout
    print(
        "REAL_BROKER_RECOVERY_OK: registered task, retained outbox, isolated old-generation deletion, duplicate replay"
    )


def configure_queue():
    from langboard_shared.core.broker import Broker

    Broker.celery.conf.update(
        task_default_queue=os.environ["CLEANUP_TEST_QUEUE"],
        task_publish_retry=False,
        broker_connection_timeout=0.5,
    )
    return Broker


def scenario(directory):
    import json
    import socket
    from hashlib import sha256
    from time import monotonic, sleep
    from uuid import uuid4
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    token = uuid4().hex
    schema = "document_cleanup_broker_" + token
    admin = create_engine(os.environ["LANGBOARD_FILE_TEST_DATABASE_URL"])
    isolated = (
        make_url(os.environ["LANGBOARD_FILE_TEST_DATABASE_URL"])
        .update_query_dict({"options": "-csearch_path=" + schema})
        .render_as_string(hide_password=False)
    )
    os.environ.update(
        MAIN_DATABASE_URL=isolated,
        READONLY_DATABASE_URL=isolated,
        CACHE_TYPE="redis",
        BROKER_URL=os.environ["LANGBOARD_TEST_CLEANUP_BROKER_URL"],
        CACHE_URL=os.environ["LANGBOARD_TEST_CLEANUP_BROKER_URL"],
        BROKER_KEY_PREFIX="cleanup-" + token + ":",
        CACHE_KEY_PREFIX="cleanup-cache-" + token + ":",
        CLEANUP_TEST_QUEUE="cleanup-" + token,
    )
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import (
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentVectorCleanup,
    )
    from langboard_shared.Env import Env
    from langboard_shared.tasks.docling import DocumentCleanupTask as outbox
    from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
    from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
    from langboard_shared.tasks.docling.DocumentSqliteVectorStore_test import Fixture
    from langboard_shared.tasks.docling.DocumentVectorStore import stage_vector_generation

    broker = configure_queue()
    worker = None
    fingerprint = sha256(token.encode()).hexdigest()
    vector_path = Env.DATA_DIR / "document-retrieval" / (fingerprint + ".sqlite")
    assert not vector_path.exists()
    worker_log = directory / "worker.log"
    log_handle = worker_log.open("w")
    try:
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        for model in (DocumentVectorCleanup, DocumentEmbeddingCache, DocumentEmbeddingCacheReference):
            model.__table__.create(DbEngine.get_main_engine())
        vector_path.parent.mkdir(parents=True, exist_ok=True)
        source = dict(
            board_uid="fixture-board",
            card_uid="fixture-card",
            attachment_uid="fixture-attachment",
            content_hash="fixture-hash",
            embedding_fingerprint=fingerprint,
        )
        with open_sqlite_vector_store(vector_path, Fixture(), dimensions=3) as store:
            old = stage_vector_generation(
                store, source=source, text="alpha old", splitter=DocumentSplitterSettings(), storage={"type": "sqlite"}
            )
            new = stage_vector_generation(
                store,
                source=source,
                text="alpha retained",
                splitter=DocumentSplitterSettings(),
                storage={"type": "sqlite"},
            )
        request = directory / "pointer.json"
        request.write_text(json.dumps(old))
        # A closed loopback port causes a real connection failure; no broker stub.
        with socket.socket() as socket_probe:
            socket_probe.bind(("127.0.0.1", 0))
            unavailable = "redis://127.0.0.1:" + str(socket_probe.getsockname()[1]) + "/0"
        failed_env = {**os.environ, "BROKER_URL": unavailable}
        writer = subprocess.run(
            [sys.executable, __file__, "record", str(request)],
            env=failed_env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert writer.returncode == 0, writer.stdout + writer.stderr
        assert "BROKER_UNAVAILABLE_CONFIRMED" in writer.stdout
        with DbSession.use(readonly=False) as db:
            event = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).first()
        assert event and event.attempt_count == 0 and event.claim_token is None
        with open_sqlite_vector_store(vector_path, None, dimensions=3) as store:
            assert store.get_by_ids(old["chunk_ids"]) and store.get_by_ids(new["chunk_ids"])
        worker = subprocess.Popen(
            [sys.executable, __file__, "worker"], env=os.environ.copy(), stdout=log_handle, stderr=subprocess.STDOUT
        )
        deadline = monotonic() + 20
        while True:
            assert worker.poll() is None, worker_log.read_text()
            try:
                broker.require_registered_tasks((outbox.TASK_NAME,), timeout=1)
                break
            except RuntimeError:
                assert monotonic() < deadline, worker_log.read_text()
                sleep(0.25)
        assert outbox.recover_cleanup() == 1
        deadline = monotonic() + 15
        while True:
            with DbSession.use(readonly=False) as db:
                retained = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).first()
            if retained is None:
                break
            assert worker.poll() is None and monotonic() < deadline, worker_log.read_text()
            sleep(0.1)
        with open_sqlite_vector_store(vector_path, None, dimensions=3) as store:
            assert store.get_by_ids(old["chunk_ids"]) == []
            assert store.get_by_ids(new["chunk_ids"])
        # Replay the completed event through the real queue; task must remain harmless.
        assert outbox.enqueue_cleanup(int(event.id))
        deadline = monotonic() + 5
        while worker_log.read_text().count("succeeded in") < 2:
            assert worker.poll() is None and monotonic() < deadline, worker_log.read_text()
            sleep(0.1)
        print("REAL_BROKER_RECOVERY_OK")
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        log_handle.close()
        if worker_log.exists():
            print(worker_log.read_text())
        broker.celery.close()
        import redis

        client = redis.Redis.from_url(os.environ["LANGBOARD_TEST_CLEANUP_BROKER_URL"])
        try:
            # Remove only this test's namespace, never flush a shared Redis database.
            for key in client.scan_iter(match=os.environ["BROKER_KEY_PREFIX"] + "*"):
                client.delete(key)
        finally:
            client.close()
            DbEngine.get_main_engine().dispose()
            try:
                with admin.begin() as connection:
                    connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            finally:
                admin.dispose()
                for suffix in ("", "-wal", "-shm"):
                    vector_path.with_name(vector_path.name + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    if sys.argv[1] == "scenario":
        scenario(Path(sys.argv[2]))
    elif sys.argv[1] == "record":
        import json
        from kombu.exceptions import OperationalError
        from langboard_shared.core.db import DbSession
        from langboard_shared.tasks.docling.DocumentCleanupTask import record_cleanup

        broker = configure_queue()
        try:
            with broker.celery.connection_for_write() as connection:
                connection.ensure_connection(max_retries=0)
        except OperationalError:
            print("BROKER_UNAVAILABLE_CONFIRMED")
        else:
            raise AssertionError("Expected the isolated broker endpoint to be unavailable")
        with DbSession.atomic() as db:
            record_cleanup(db, json.loads(Path(sys.argv[2]).read_text()))
    elif sys.argv[1] == "worker":
        from langboard_shared.tasks.docling.DocumentCleanupTask import TASK_NAME

        broker = configure_queue()
        assert TASK_NAME in broker.celery.tasks
        broker.celery.worker_main(
            [
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=info",
                "--without-gossip",
                "--without-mingle",
                "-Q",
                os.environ["CLEANUP_TEST_QUEUE"],
                "--hostname=cleanup-fixture@localhost",
            ]
        )
