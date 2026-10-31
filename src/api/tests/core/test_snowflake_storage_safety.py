"""Generated IDs retain compatibility; the database arbitrates worker collisions."""

import importlib
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4
import pytest
from bcrypt import gensalt, hashpw
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


FIXTURE_PASSWORD_HASH = hashpw(b"test-only", gensalt(rounds=4)).decode()


module = importlib.import_module("langboard_shared.core.types.SnowflakeID")
db_module = importlib.import_module("langboard_shared.core.db.DbSession")


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(SnowflakeID, "_last_timestamp", -1)
    monkeypatch.setattr(SnowflakeID, "_sequence", 0)
    monkeypatch.setattr(SnowflakeID, "_machine_id", 950)
    monkeypatch.setattr(SnowflakeID, "_current_millis", classmethod(lambda cls: cls.EPOCH + 100000))


def test_same_millisecond_threads_sequence_overflow_and_short_codes(fixed_clock):
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: SnowflakeID(), range(8193)))
    assert len(set(values)) == len(values)
    assert {(int(value) >> 12) & 0x3FF for value in values} == {950}
    assert {int(value) >> 22 for value in values} == {100000, 100001, 100002}
    assert all(SnowflakeID.from_short_code(value.to_short_code()) == value for value in values)
    for value in (0, 1, 123456789, SnowflakeID.MAX_VALUE):
        assert SnowflakeID.from_short_code(SnowflakeID(value).to_short_code()) == value


def test_clock_rollback_preserves_last_sequence(fixed_clock, monkeypatch):
    first = SnowflakeID()
    monkeypatch.setattr(SnowflakeID, "_current_millis", classmethod(lambda cls: cls.EPOCH + 99999))
    assert SnowflakeID() == first + 1


@pytest.mark.parametrize("timestamp", [SnowflakeID.EPOCH - 1, SnowflakeID.EPOCH + 2**41])
def test_timestamp_outside_signed_range_is_rejected(fixed_clock, monkeypatch, timestamp):
    monkeypatch.setattr(SnowflakeID, "_current_millis", classmethod(lambda cls: timestamp))
    with pytest.raises(OverflowError):
        SnowflakeID()


def test_fork_discards_inherited_lock_and_node_identity(fixed_clock):
    old_lock = SnowflakeID._lock
    module._reset_after_fork()
    assert SnowflakeID._lock is not old_lock
    assert SnowflakeID._machine_id is None
    assert SnowflakeID._last_timestamp == -1
    assert SnowflakeID._sequence == 0


@pytest.fixture(params=["sqlite", "postgresql"])
def storage_engine(request):
    admin = None
    schema = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Dedicated PostgreSQL proof URL not set")
        admin = create_engine(url)
        schema = f"snowflake_{uuid4().hex}"
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine("sqlite://")
    User.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text('CREATE UNIQUE INDEX fixture_email_unique ON "user" (email)'))
    try:
        yield engine
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


def _forked_allocate(pipe):
    pipe.send(int(SnowflakeID()))
    pipe.close()


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="No fork support")
def test_real_fork_while_parent_lock_is_held(fixed_clock):
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    SnowflakeID._lock.acquire()
    process = context.Process(target=_forked_allocate, args=(child,))
    try:
        process.start()
        assert parent.poll(5), "Child inherited a locked allocator"
        value = parent.recv()
        assert value > 0
        process.join(5)
        assert process.exitcode == 0
    finally:
        SnowflakeID._lock.release()
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()


def make_user(email):
    return User(
        firstname="ID",
        lastname="Fixture",
        email=email,
        username="fixture-" + email.split("@", 1)[0],
        password=FIXTURE_PASSWORD_HASH,
    )


def test_database_retries_only_id_conflicts_and_keeps_transaction_usable(monkeypatch, storage_engine):
    engine = storage_engine
    identifiers = iter([1, 1, 2, 3])
    monkeypatch.setattr(
        db_module, "SnowflakeID", lambda *args: SnowflakeID(args[0]) if args else SnowflakeID(next(identifiers))
    )
    try:
        with Session(engine) as session, session.begin():
            db = DbSession(session, readonly=False)
            first, second = make_user("first@example.invalid"), make_user("second@example.invalid")
            db.insert(first)
            db.insert(second)
            assert [first.id, second.id] == [1, 2]
            assert len(session.execute(select(User.__table__)).all()) == 2
            with pytest.raises(IntegrityError), session.begin_nested():
                db.insert(make_user("first@example.invalid"))
            assert len(session.execute(select(User.__table__)).all()) == 2
    finally:
        engine.dispose()


def test_id_allocation_exhaustion_preserves_existing_row_and_resets_new_model(monkeypatch, storage_engine):
    engine = storage_engine
    monkeypatch.setattr(db_module, "SnowflakeID", lambda *args: SnowflakeID(args[0] if args else 1))
    try:
        with Session(engine) as session, session.begin():
            db = DbSession(session, readonly=False)
            db.insert(make_user("first@example.invalid"))
            second = make_user("second@example.invalid")
            with pytest.raises(RuntimeError, match="allocation exhausted"):
                db.insert(second)
            assert second.id == 0
            assert len(session.execute(select(User.__table__)).all()) == 1
    finally:
        engine.dispose()


def _persist_worker_batch(database_url, worker, queue, ready, start, batch_size=8):
    SnowflakeID._last_timestamp = -1
    SnowflakeID._sequence = 0
    SnowflakeID._machine_id = 950
    SnowflakeID._current_millis = classmethod(lambda cls: cls.EPOCH + 100000)
    engine = create_engine(database_url)
    try:
        ready.put(worker)
        if not start.wait(60):
            raise TimeoutError("Parent did not release the storage workers")
        with Session(engine) as session, session.begin():
            db = DbSession(session, readonly=False)
            users = [make_user(f"worker-{worker}-{index}@example.invalid") for index in range(batch_size)]
            db.insert_all(users)
        queue.put([int(user.id) for user in users])
    except Exception as error:
        queue.put({"error": type(error).__name__, "detail": str(error)})
        raise
    finally:
        engine.dispose()


@pytest.mark.parametrize("batch_size", [8, 64])
def test_independent_processes_with_same_node_and_clock_persist_unique_rows(tmp_path, batch_size, storage_engine):
    if storage_engine.dialect.name == "postgresql":
        engine = storage_engine
        with engine.connect() as connection:
            schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        url = engine.url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    else:
        url = f"sqlite:///{tmp_path / 'worker-ids.sqlite'}"
        engine = create_engine(url)
        User.__table__.create(engine)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    ready = context.Queue()
    start = context.Event()
    workers = [
        context.Process(target=_persist_worker_batch, args=(url, worker, queue, ready, start, batch_size))
        for worker in range(4)
    ]
    try:
        for worker in workers:
            worker.start()
        # Cold interpreter imports are not part of the concurrent storage budget.
        assert {ready.get(timeout=60) for _ in workers} == set(range(4))
        start.set()
        batches = [queue.get(timeout=20) for _ in workers]
        assert all(isinstance(batch, list) for batch in batches), batches
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
        identifiers = [value for batch in batches for value in batch]
        assert len(identifiers) == len(set(identifiers)) == 4 * batch_size
        with engine.connect() as connection:
            assert len(connection.execute(select(User.__table__)).all()) == 4 * batch_size
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        queue.close()
        queue.join_thread()
        ready.close()
        ready.join_thread()
        engine.dispose()


def test_collision_skip_preserves_node_rollback_and_63bit_boundary(fixed_clock, monkeypatch):
    first = SnowflakeID()
    SnowflakeID.advance_after_collision(first)
    skipped = SnowflakeID()
    assert int(skipped) >> 22 == (int(first) >> 22) + 1
    assert ((int(skipped) >> 12) & 0x3FF) == 950 and int(skipped) & 0xFFF == 0
    SnowflakeID.advance_after_collision(first)
    assert SnowflakeID() == skipped + 1
    SnowflakeID.advance_after_collision(SnowflakeID.MAX_VALUE)
    with pytest.raises(OverflowError):
        SnowflakeID()


def test_allocator_hot_path_measurement(record_property):
    from time import perf_counter

    started = perf_counter()
    values = [SnowflakeID() for _ in range(100000)]
    elapsed = perf_counter() - started
    assert len(set(values)) == len(values)
    record_property("allocator_ids_per_second", 100000 / elapsed)
    print(f"100000 IDs in {elapsed:.4f}s; {100000 / elapsed:.0f} IDs/s")


def test_used_sequence_range_exhausts_old_retry_but_collision_skip_recovers(storage_engine, fixed_clock, monkeypatch):
    engine = storage_engine
    with Session(engine) as session, session.begin():
        db = DbSession(session, readonly=False)
        db.insert_all([make_user(f"seed-{index}@example.invalid") for index in range(64)])
        SnowflakeID._last_timestamp = -1
        SnowflakeID._sequence = 0
        candidate = make_user("after-collision@example.invalid")
        with monkeypatch.context() as patch:
            patch.setattr(SnowflakeID, "advance_after_collision", lambda value: None)
            with pytest.raises(RuntimeError, match="allocation exhausted"):
                db.insert(candidate)
            assert candidate.id == 0
        db.insert(candidate)
        assert int(candidate.id) >> 22 == 100001
        assert len(session.execute(select(User.__table__)).all()) == 65


@pytest.fixture
def attachment_storage(storage_engine, tmp_path, monkeypatch):
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.storage.LocalStorage import LocalStorage
    from langboard_shared.domain.models import AttachmentStorageObject, AttachmentStorageReference
    from langboard_shared.Env import Env

    AttachmentStorageObject.__table__.create(storage_engine)
    AttachmentStorageReference.__table__.create(storage_engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: storage_engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    return LocalStorage(), storage_engine


def test_native_attachment_upload_recovers_id_collision_and_preserves_content_reuse(attachment_storage, fixed_clock):
    from langboard_shared.core.storage import StorageName
    from langboard_shared.domain.models import AttachmentStorageObject, AttachmentStorageReference
    from langboard_shared.domain.services import AttachmentStorageOperations as operations

    storage, engine = attachment_storage
    first = operations.upload(storage, BytesIO(b"original"), StorageName.CardAttachment, "first.txt", "first.txt")
    SnowflakeID._last_timestamp = -1
    SnowflakeID._sequence = 0
    second = operations.upload(storage, BytesIO(b"different"), StorageName.CardAttachment, "second.txt", "second.txt")
    SnowflakeID._last_timestamp = -1
    SnowflakeID._sequence = 0
    duplicate = operations.upload(storage, BytesIO(b"different"), StorageName.CardAttachment, "third.txt", "third.txt")
    with engine.connect() as connection:
        objects = connection.execute(select(AttachmentStorageObject.__table__)).mappings().all()
        references = connection.execute(select(AttachmentStorageReference.__table__)).mappings().all()
    assert len(objects) == 2 and len(references) == 3
    object_ids = {reference["filename"]: reference["object_id"] for reference in references}
    assert object_ids["first.txt"] != object_ids["second.txt"] == object_ids["third.txt"]
    for file, expected in ((first, b"original"), (second, b"different"), (duplicate, b"different")):
        managed, locator = operations.resolve(storage.storage_type, file.storage_name, file.filename)
        assert managed and storage.get(*locator) == expected


def test_native_attachment_id_exhaustion_preserves_existing_object(attachment_storage, fixed_clock, monkeypatch):
    from langboard_shared.core.storage import StorageName
    from langboard_shared.domain.models import AttachmentStorageObject, AttachmentStorageReference
    from langboard_shared.domain.services import AttachmentStorageOperations as operations

    storage, engine = attachment_storage
    original = operations.upload(storage, BytesIO(b"original"), StorageName.CardAttachment, "first.txt", "first.txt")
    with engine.connect() as connection:
        original_object = connection.execute(select(AttachmentStorageObject.__table__)).mappings().one()
    allocator = Mock(
        return_value=SnowflakeID(original_object["id"]), advance_after_collision=SnowflakeID.advance_after_collision
    )
    monkeypatch.setattr(operations, "SnowflakeID", allocator)
    with pytest.raises(RuntimeError, match="identity allocation exhausted"):
        operations.upload(storage, BytesIO(b"different"), StorageName.CardAttachment, "second.txt", "second.txt")
    assert allocator.call_count == 32
    with engine.connect() as connection:
        assert connection.execute(select(AttachmentStorageObject.__table__)).mappings().one() == original_object
        assert len(connection.execute(select(AttachmentStorageReference.__table__)).all()) == 1
    managed, locator = operations.resolve(storage.storage_type, original.storage_name, original.filename)
    assert managed and storage.get(*locator) == b"original"
