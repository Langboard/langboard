"""Native byte ownership; controlled provider faults never confer read authority."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, text, update
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel, Storage, StorageName
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import AttachmentStorageObject, AttachmentStorageReference
from langboard_shared.domain.services import AttachmentStorageOperations as operations
from langboard_shared.Env import Env


@pytest.fixture(params=["sqlite", "postgresql-test"])
def registry(request, tmp_path, monkeypatch):
    schema = None
    if request.param == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to an owned disposable PostgreSQL database")
        engine = create_engine(url)
        schema = "physical_reuse_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'objects.db'}", connect_args={"check_same_thread": False})
    for model in (AttachmentStorageObject, AttachmentStorageReference):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("Registry must use the current primary"))
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    native = Storage._storages["local"].upload_immutable
    writes = Mock(wraps=native)
    monkeypatch.setattr(Storage._storages["local"], "upload_immutable", writes)
    yield engine, writes
    if schema:
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def add(name=None, payload=b"identical", namespace=StorageName.CardAttachment):
    return Storage.upload_named(BytesIO(payload), namespace, name or "ref_" + uuid4().hex + ".pdf", "original.pdf")


def rows(engine, model):
    with engine.connect() as db:
        return db.execute(select(model.__table__)).mappings().all()


def test_distinct_native_locators_share_one_physical_write_and_release_independently(registry):
    engine, writes = registry
    files = [add(namespace=namespace) for namespace in (StorageName.CardAttachment, StorageName.ProjectAttachment, StorageName.Wiki)]
    assert len({file.path for file in files}) == 3
    assert len({(file.storage_name, file.filename) for file in files}) == 3
    assert all(Storage.get_file(file) == b"identical" for file in files)
    assert len(rows(engine, AttachmentStorageObject)) == writes.call_count == 1
    assert len(rows(engine, AttachmentStorageReference)) == 3
    assert Storage.delete(files[0]) and Storage.delete(files[0])
    assert Storage.get_file(files[0]) is None
    assert all(Storage.get_file(file) == b"identical" for file in files[1:])
    with DbSession.use(readonly=False) as db:
        for model in (AttachmentStorageObject, AttachmentStorageReference):
            private = db.exec(SqlBuilder.select.table(model)).first()
            assert private.api_response() == private.notification_data() == {}
            assert not {"content_sha256", "allocation_token", "filename", "object_id"} & private.model_dump().keys()


def test_named_retry_cannot_change_payload_or_revive_released_locator(registry):
    _, writes = registry
    first = add("ref_retry.pdf")
    assert add("ref_retry.pdf").filename == first.filename
    with pytest.raises(ValueError, match="another operation"):
        add("ref_retry.pdf", b"different")
    assert Storage.get_file(first) == b"identical" and writes.call_count == 1
    assert Storage.delete(first)
    with pytest.raises(ValueError, match="another operation"):
        add("ref_retry.pdf")


def test_concurrent_identical_acquires_allocate_one_object(registry):
    engine, writes = registry
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(lambda _: add(), range(4)))
    assert len({file.filename for file in files}) == 4
    assert writes.call_count == 1
    assert len(rows(engine, AttachmentStorageReference)) == 4
    assert all(Storage.get_file(file) == b"identical" for file in files)


def test_provider_success_database_rollback_is_verified_and_reused(registry):
    engine, writes = registry
    with pytest.raises(RuntimeError, match="publication lost"):
        with DbSession.atomic():
            failed = add()
            raise RuntimeError("publication lost")
    assert not rows(engine, AttachmentStorageObject) and not rows(engine, AttachmentStorageReference)
    assert Storage.get_file(failed) is None
    assert Storage.delete(failed)
    recovered = add()
    assert Storage.get_file(recovered) == b"identical"
    assert writes.call_count == 1


def test_stale_publisher_cannot_create_a_logical_reference(registry, monkeypatch):
    engine, writes = registry
    native = operations._publish

    def steal(db, object_id, token):
        db.exec(update(AttachmentStorageObject).where(AttachmentStorageObject.id == object_id).values(allocation_token="x" * 32))
        native(db, object_id, token)

    with monkeypatch.context() as patch:
        patch.setattr(operations, "_publish", steal)
        with pytest.raises(RuntimeError, match="lost publication authority"):
            add()
    assert not rows(engine, AttachmentStorageReference)
    assert Storage.get_file(add()) == b"identical" and writes.call_count == 1


def test_process_exit_after_provider_success_recovers_without_another_write(registry):
    engine, writes = registry
    schema = None
    if engine.dialect.name == "postgresql":
        with engine.connect() as db:
            schema = db.execute(text("SELECT current_schema()")).scalar_one()
    probe = """
import os, sys
from pathlib import Path
from io import BytesIO
from sqlalchemy import create_engine
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.services import AttachmentStorageOperations as operations
from langboard_shared.Env import Env
options = {'connect_args': {'options': '-csearch_path=' + sys.argv[3]}} if sys.argv[3] else {}
engine = create_engine(sys.argv[1], **options)
DbEngine.get_main_engine = lambda: engine
type(Env).LOCAL_STORAGE_DIR = property(lambda _: Path(sys.argv[2]))
Storage._storages = {'local': LocalStorage()}
operations._publish = lambda *_: os._exit(73)
Storage.upload_named(BytesIO(b'identical'), StorageName.CardAttachment, 'ref_lost.pdf', 'original.pdf')
raise AssertionError('Process exit boundary not reached')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, engine.url.render_as_string(hide_password=False), str(Env.LOCAL_STORAGE_DIR), schema or ""],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 73, result.stderr
    assert not rows(engine, AttachmentStorageObject) and not rows(engine, AttachmentStorageReference)
    recovered = add()
    assert Storage.get_file(recovered) == b"identical"
    assert writes.call_count == 0
    assert len(list((Env.LOCAL_STORAGE_DIR / operations.PRIVATE_NAMESPACE).iterdir())) == 1


def test_partial_provider_publication_is_never_registered(registry, monkeypatch):
    engine, writes = registry
    storage = Storage._storages["local"]
    native = storage.upload_immutable

    def truncated(file, filename, namespace, key):
        return native(BytesIO(b"partial"), filename, namespace, key)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "upload_immutable", truncated)
        with pytest.raises(RuntimeError, match="verification"):
            add()
    assert not rows(engine, AttachmentStorageReference)
    # An incomplete immutable object must never be silently overwritten or read.
    assert Storage.get_file(FileModel(storage_type="local", storage_name=operations.PRIVATE_NAMESPACE,
                                     filename=operations._physical_filename(sha256(b"identical").hexdigest(), 9),
                                     original_filename="private", path="private")) is None


def test_ready_object_failure_cannot_publish_or_overwrite_shared_bytes(registry):
    engine, writes = registry
    first = add()
    _, physical = operations.resolve(first.storage_type, first.storage_name, first.filename)
    target = Env.LOCAL_STORAGE_DIR / physical[0] / physical[1]
    target.write_bytes(b"fixture external corruption")
    with pytest.raises(RuntimeError, match="Ready attachment"):
        add()
    assert writes.call_count == 1 and len(rows(engine, AttachmentStorageReference)) == 1
    assert target.read_bytes() == b"fixture external corruption"


def test_pending_owner_survives_other_release_and_rollback(registry):
    _, writes = registry
    first = add()
    pending = Event()
    finish = Event()

    def acquire():
        with DbSession.atomic():
            second = add()
            pending.set()
            assert finish.wait(5)
        return second

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(acquire)
        assert pending.wait(5)
        release = pool.submit(Storage.delete, first)
        finish.set()
        second = future.result(timeout=10)
        assert release.result(timeout=10)
    assert Storage.get_file(first) is None and Storage.get_file(second) == b"identical"
    assert Storage.delete(first) and writes.call_count == 1


def test_private_object_locators_cannot_use_facade_or_http(registry):
    from types import SimpleNamespace
    from langboard.routes.file import FileApi
    from langboard_shared.core.routing import ApiException

    engine, _ = registry
    file = add()
    physical = rows(engine, AttachmentStorageObject)[0]
    key = operations._physical_filename(physical["content_sha256"], physical["size_bytes"])
    encrypted = file.path.split("/")[2]
    assert Storage.get(encrypted, operations.PRIVATE_NAMESPACE, key) is None
    assert not Storage.download(encrypted, operations.PRIVATE_NAMESPACE, key, BytesIO())
    forged = file.model_copy(update={"storage_name": operations.PRIVATE_NAMESPACE, "filename": key})
    assert not Storage.delete(forged)
    with pytest.raises(ApiException.NotFound_404):
        FileApi.get_file(SimpleNamespace(scope={}), encrypted, operations.PRIVATE_NAMESPACE, key, object())
    assert Storage.get_file(file) == b"identical"


def test_legacy_unmanaged_source_retains_native_get_and_delete(registry):
    storage = Storage._storages["local"]
    legacy = storage.upload_named(BytesIO(b"legacy"), "legacy.pdf", StorageName.CardAttachment, "legacy.pdf")
    assert Storage.get_file(legacy) == b"legacy"
    assert Storage.delete(legacy) and Storage.get_file(legacy) is None


def test_managed_upload_cannot_shadow_legacy_locator(registry):
    _, writes = registry
    storage = Storage._storages["local"]
    legacy = storage.upload_named(BytesIO(b"legacy"), "legacy.pdf", StorageName.CardAttachment, "legacy.pdf")
    with pytest.raises(ValueError, match="unmanaged legacy"):
        add("legacy.pdf")
    assert Storage.get_file(legacy) == b"legacy" and writes.call_count == 0


def test_stream_is_bounded_and_size_limit_precedes_provider_write(registry, monkeypatch):
    _, writes = registry

    class Bounded(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            return super().read(size)

    monkeypatch.setattr(type(Env), "MAX_FILE_SIZE_MB", property(lambda _: 1))
    with pytest.raises(ValueError, match="upload limit"):
        Storage.upload_named(Bounded(b"x" * (1024 * 1024 + 1)), StorageName.Wiki, "ref_oversized.pdf", "original.pdf")
    assert writes.call_count == 0
    stored = Storage.upload_named(Bounded(b"x" * 1024), StorageName.Wiki, "ref_bounded.pdf", "original.pdf")
    assert Storage.get_file(stored) == b"x" * 1024 and writes.call_count == 1


def test_additive_migration_preserves_populated_sources_and_refuses_owned_downgrade(registry):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine, _ = registry
    AttachmentStorageReference.__table__.drop(engine)
    AttachmentStorageObject.__table__.drop(engine)
    path = Path(__file__).parents[6] / "src/api/langboard/migrations/versions/20261022230000-a570fb29ed43.py"
    spec = spec_from_file_location("physical_reuse_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as db:
        db.execute(text("CREATE TABLE legacy_sources (id INTEGER PRIMARY KEY, file TEXT, cache_key TEXT)"))
        db.execute(text("INSERT INTO legacy_sources VALUES (1, '/original/private/url', 'original-cache')"))
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
        assert db.execute(text("SELECT file, cache_key FROM legacy_sources")).one() == ("/original/private/url", "original-cache")
    add()
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="retained"):
            migration.downgrade()
