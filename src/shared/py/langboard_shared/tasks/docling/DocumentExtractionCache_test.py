"""Real primary SQL lifecycle and concurrency; remote conversion is an explicit fixture."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from os import environ
from pathlib import Path
from threading import Event, local
from unittest.mock import Mock
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, update
from sqlalchemy.schema import CreateSchema, DropSchema
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import DocumentExtractionCache as Cache
from langboard_shared.domain.models import DocumentExtractionCacheReference as Reference
from langboard_shared.tasks.docling import DocumentExtractionCache as module
from langboard_shared.tasks.docling.DocumentExtractionCache import (
    CachedDocumentExtraction,
    ExtractionCacheUnavailable,
    activate_extraction_reference,
    cached_document_extraction,
    recover_expired_extraction_references,
    release_extraction_source,
)


@pytest.fixture(params=["sqlite", "postgresql"])
def engine_store(request, monkeypatch, tmp_path):
    schema = None
    if request.param == "postgresql":
        url = environ.get("LANGBOARD_EXTRACTION_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Dedicated PostgreSQL fixture not supplied")
        base_engine = create_engine(url, connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=10000"})
        schema = "extraction_test_" + uuid4().hex
        with base_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = base_engine.execution_options(schema_translate_map={None: schema})
    else:
        base_engine = create_engine(f"sqlite:///{tmp_path / 'extraction.sqlite'}", connect_args={"timeout": 10})
        engine = base_engine
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    yield engine
    if schema:
        with base_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
    base_engine.dispose()


@pytest.fixture
def store(engine_store):
    Cache.__table__.create(engine_store)
    Reference.__table__.create(engine_store)
    return engine_store


def settings(**changes):
    return (
        dict(
            binding_uid="binding",
            content_hash=sha256(b"verified bytes").hexdigest(),
            vision_config={"binding_uid": "binding", "model_name": "vision"},
            filename="private.pdf",
            timeout_seconds=2,
        )
        | changes
    )


def converted():
    assert not DbSession.has_active_transaction()
    return {
        "markdown": "private result",
        "keywords": {"en": ["  keyword ", "KEYWORD"]},
        "document_json": {
            "schema_name": "DoclingDocument",
            "name": "/private/path/file.pdf",
            "origin": {"filename": "/tmp/private-file.pdf"},
        },
    }


def rows(model):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model)).all()


def activate(cache, source="source", generation="a" * 32):
    with DbSession.atomic() as db:
        assert activate_extraction_reference(
            db, cache.token, source_type="card_attachment", source_uid=source, generation=generation
        )


def test_reuse_normalizes_and_isolates_callers(store):
    converter = Mock(side_effect=converted)
    with cached_document_extraction(**settings()) as first:
        result = first.get_or_convert(converter)
        activate(first)
        result["document_json"]["origin"]["filename"] = "leaked"
        result["keywords"]["en"].append("leaked")
    with cached_document_extraction(**settings(filename="different.PDF")) as second:
        result = second.get_or_convert(converter)
        assert result["document_json"]["name"] == "document.pdf"
        assert result["document_json"]["origin"]["filename"] == "document.pdf"
        assert result["keywords"] == {"en": ["keyword"]}
        assert second.token != first.token
    assert converter.call_count == 1
    assert len(rows(Cache)) == len(rows(Reference)) == 1
    assert rows(Cache)[0].api_response() == {}


def test_identity_separates_bindings_format_settings_hash_and_parser(store, monkeypatch):
    convert = Mock(side_effect=converted)
    variants = [
        {},
        {"filename": "same.docx"},
        {"content_hash": sha256(b"other").hexdigest()},
        {"vision_config": {"binding_uid": "binding", "model_name": "other"}},
        {"binding_uid": "other", "vision_config": {"binding_uid": "other"}},
    ]
    for variant in variants:
        with cached_document_extraction(**settings(**variant)) as cache:
            cache.get_or_convert(convert)
            cache.retain()
    monkeypatch.setattr(module.metadata, "version", lambda _: "changed-runtime")
    with cached_document_extraction(**settings()) as cache:
        cache.get_or_convert(convert)
        cache.retain()
    monkeypatch.setattr(module, "CONVERSION_VERSION", "changed-contract")
    with cached_document_extraction(**settings()) as cache:
        cache.get_or_convert(convert)
        cache.retain()
    assert convert.call_count == 7
    assert len(rows(Cache)) == 7


@pytest.mark.parametrize("runtime", ["pillow", "pypdfium2", "lxml"])
def test_parser_or_renderer_version_alone_invalidates_identical_content_and_settings(store, monkeypatch, runtime):
    versions = {name: "1.0" for name in ("docling", "docling-core", "docling-slim", "pillow", "pypdfium2", "lxml")}
    monkeypatch.setattr(module.metadata, "version", versions.__getitem__)
    convert = Mock(side_effect=converted)
    original_key = None
    for _ in range(2):
        with cached_document_extraction(**settings()) as cache:
            cache.get_or_convert(convert)
            cache.retain()
            original_key = cache.key
    assert convert.call_count == 1
    versions[runtime] = "2.0"
    assert all(versions[name] == "1.0" for name in ("docling", "docling-core", "docling-slim"))
    for _ in range(2):
        with cached_document_extraction(**settings()) as cache:
            cache.get_or_convert(convert)
            cache.retain()
            assert cache.key != original_key
    assert convert.call_count == 2
    assert len(rows(Cache)) == 2


def test_concurrent_ready_and_claim_reuse_one_conversion(store):
    started, waiting, release = Event(), Event(), Event()
    calls = []

    def converter():
        calls.append(True)
        started.set()
        assert release.wait(5)
        return converted()

    def worker(observe=False):
        with cached_document_extraction(**settings(on_validate=waiting.set if observe else None)) as cache:
            result = cache.get_or_convert(converter)
            cache.retain()
            return result

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(worker)
        assert started.wait(5)
        second = pool.submit(worker, True)
        assert waiting.wait(5)
        release.set()
        assert first.result(10) == second.result(10)
    assert len(calls) == 1
    assert len(rows(Reference)) == 2


def test_simultaneous_new_claims_recover_unique_collision(store):
    from threading import Barrier

    barrier = Barrier(2)
    convert = Mock(side_effect=converted)

    def worker():
        with cached_document_extraction(**settings()) as cache:
            barrier.wait(timeout=5)
            result = cache.get_or_convert(convert)
            cache.retain()
            return result

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        assert futures[0].result(10) == futures[1].result(10)
    assert convert.call_count == 1


def test_stale_lease_cannot_publish_or_clobber_winner(store):
    with cached_document_extraction(**settings()) as loser:

        def steal():
            with DbSession.atomic() as db:
                db.exec(
                    update(Cache)
                    .where(Cache.cache_key == loser.key)
                    .values(
                        lease_token="b" * 32,
                        lease_expires_at=SafeDateTime.now() + timedelta(seconds=30),
                        markdown="winner",
                        keywords={},
                    )
                )
            return converted()

        with pytest.raises(ExtractionCacheUnavailable, match="lease"):
            loser.get_or_convert(steal)
        assert rows(Cache)[0].markdown == "winner"
    # A live foreign lease must survive orphan cleanup.
    assert rows(Cache)[0].lease_token == "b" * 32
    with DbSession.atomic() as db:
        db.exec(update(Cache).values(lease_expires_at=SafeDateTime.now() - timedelta(seconds=1)))
    assert recover_expired_extraction_references() == 0
    assert rows(Cache) == []


def test_failed_conversion_and_unpublished_success_remove_orphans(store):
    converter = Mock(side_effect=[RuntimeError("provider"), converted()])
    with pytest.raises(RuntimeError, match="provider"):
        with cached_document_extraction(**settings()) as cache:
            cache.get_or_convert(converter)
    assert rows(Cache) == rows(Reference) == []
    with cached_document_extraction(**settings()) as cache:
        assert cache.get_or_convert(converter)["markdown"] == "private result"
    assert converter.call_count == 2
    assert rows(Cache) == rows(Reference) == []


def test_successful_extraction_survives_publication_rollback_for_retry(store):
    converter = Mock(side_effect=converted)
    with pytest.raises(RuntimeError, match="publication"):
        with cached_document_extraction(**settings()) as first:
            first.get_or_convert(converter)
            expiry = rows(Reference)[0].expires_at
            with DbSession.atomic() as db:
                assert activate_extraction_reference(
                    db, first.token, source_type="card_attachment", source_uid="source", generation="a" * 32
                )
                raise RuntimeError("publication")
    reference = rows(Reference)[0]
    assert reference.expires_at == expiry
    assert reference.source_type is reference.source_uid is reference.generation is None
    assert reference.api_response() == rows(Cache)[0].api_response() == {}
    assert rows(Cache)[0].lease_token is None
    with cached_document_extraction(**settings(filename="retry.PDF")) as retry:
        assert retry.get_or_convert(converter)["markdown"] == "private result"
        assert retry.token != first.token
        activate(retry)
    assert converter.call_count == 1
    references = {ref.use_token: ref for ref in rows(Reference)}
    assert references[first.token].expires_at == expiry
    assert references[retry.token].expires_at is None
    assert references[retry.token].source_uid == "source"


@pytest.mark.parametrize("source_type", ["card_attachment", "project_attachment", "wiki_attachment"])
def test_failed_publication_pending_use_survives_source_release_then_expires(store, source_type):
    converter = Mock(side_effect=converted)
    with cached_document_extraction(**settings()) as original:
        original.get_or_convert(converter)
        with DbSession.atomic() as db:
            assert activate_extraction_reference(
                db, original.token, source_type=source_type, source_uid="source", generation="a" * 32
            )
    with pytest.raises(RuntimeError, match="publication"):
        with cached_document_extraction(**settings()) as failed:
            failed.get_or_convert(converter)
            raise RuntimeError("publication")
    assert len(rows(Reference)) == 2
    with DbSession.atomic() as db:
        assert release_extraction_source(db, source_type, "source") == 1
    assert len(rows(Cache)) == len(rows(Reference)) == 1
    pending = rows(Reference)[0]
    assert pending.use_token == failed.token
    assert pending.expires_at is not None
    expiry = pending.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    remaining = expiry - SafeDateTime.now()
    assert timedelta() < remaining <= timedelta(hours=module.PENDING_REFERENCE_HOURS)
    with cached_document_extraction(**settings()) as retry:
        assert retry.get_or_convert(converter)["markdown"] == "private result"
    assert converter.call_count == 1
    with DbSession.atomic() as db:
        db.exec(update(Reference).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
        assert not activate_extraction_reference(
            db, failed.token, source_type=source_type, source_uid="source", generation="b" * 32
        )
    assert recover_expired_extraction_references(limit=1) == 1
    assert rows(Cache) == rows(Reference) == []
    with cached_document_extraction(**settings()) as fresh:
        fresh.get_or_convert(converter)
    assert converter.call_count == 2


@pytest.mark.parametrize("revoked_at", [1, 2, 3])
def test_publication_retry_revalidates_authority_before_cached_delivery(store, revoked_at):
    converter = Mock(side_effect=converted)
    with pytest.raises(RuntimeError, match="publication"):
        with cached_document_extraction(**settings()) as first:
            first.get_or_convert(converter)
            raise RuntimeError("publication")
    validate = Mock(side_effect=[None] * (revoked_at - 1) + [InterruptedError("authority revoked")])
    with pytest.raises(InterruptedError, match="authority revoked"):
        with cached_document_extraction(**settings(on_validate=validate)) as retry:
            retry.get_or_convert(converter)
            pytest.fail("Revoked authority must not receive the cached result")
    assert validate.call_count == revoked_at
    assert converter.call_count == 1
    assert [ref.use_token for ref in rows(Reference)] == [first.token]
    assert rows(Reference)[0].expires_at is not None


def test_revocation_after_conversion_discards_unvalidated_result(store):
    allowed = True

    def validate():
        if not allowed:
            raise InterruptedError("authority revoked")

    def convert():
        nonlocal allowed
        allowed = False
        return converted()

    with pytest.raises(InterruptedError, match="authority revoked"):
        with cached_document_extraction(**settings(on_validate=validate)) as cache:
            cache.get_or_convert(convert)
    assert rows(Cache) == rows(Reference) == []


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"markdown": 2},
        {"markdown": "x", "document_json": {}},
        {"markdown": "x" * (8 * 1024 * 1024 + 1)},
        {"markdown": "x", "document_json": {"schema_name": "DoclingDocument", "large": "x" * (8 * 1024 * 1024)}},
    ],
)
def test_malformed_and_oversized_results_never_cache(store, result):
    with pytest.raises(ValueError):
        with cached_document_extraction(**settings()) as cache:
            cache.get_or_convert(lambda: result)
    assert rows(Cache) == rows(Reference) == []


def test_active_pending_lifecycle_rollback_replacement_and_final_delete(store):
    with cached_document_extraction(**settings()) as first:
        first.get_or_convert(converted)
        activate(first, "one")
    with cached_document_extraction(**settings()) as second:
        second.get_or_convert(converted)
        activate(second, "two")
    with cached_document_extraction(**settings()) as pending:
        pending.get_or_convert(converted)
        pending.retain()
    with pytest.raises(RuntimeError):
        with DbSession.atomic() as db:
            assert release_extraction_source(db, "card_attachment", "one") == 1
            raise RuntimeError("rollback native removal")
    assert len(rows(Reference)) == 3
    with DbSession.atomic() as db:
        release_extraction_source(db, "card_attachment", "one")
        release_extraction_source(db, "card_attachment", "two")
    assert len(rows(Cache)) == len(rows(Reference)) == 1
    with DbSession.atomic() as db:
        db.exec(update(Reference).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
        assert not activate_extraction_reference(
            db, pending.token, source_type="card_attachment", source_uid="one", generation="a" * 32
        )
    assert recover_expired_extraction_references(limit=1) == 1
    assert rows(Cache) == rows(Reference) == []


def test_new_generation_replaces_old_ref_only_with_successful_transaction(store):
    with cached_document_extraction(**settings()) as first:
        first.get_or_convert(converted)
        activate(first)
    with cached_document_extraction(**settings(content_hash=sha256(b"new").hexdigest())) as new:
        new.get_or_convert(converted)
        with pytest.raises(RuntimeError):
            with DbSession.atomic() as db:
                assert activate_extraction_reference(
                    db, new.token, source_type="card_attachment", source_uid="source", generation="b" * 32
                )
                raise RuntimeError("native publish failed")
        assert len(rows(Cache)) == 2
        assert len([ref for ref in rows(Reference) if ref.expires_at is None]) == 1
        activate(new, generation="b" * 32)
    assert len(rows(Cache)) == len(rows(Reference)) == 1
    with DbSession.atomic() as db:
        assert release_extraction_source(db, "card_attachment", "source") == 1
    assert rows(Cache) == []


def test_validation_runs_outside_transactions_and_blocks_cached_delivery(store):
    validations = []

    def validate():
        assert not DbSession.has_active_transaction()
        validations.append(True)

    with cached_document_extraction(**settings(on_validate=validate)) as first:
        first.get_or_convert(converted)
        first.retain()
    assert len(validations) >= 4
    with pytest.raises(RuntimeError, match="authority"):
        with cached_document_extraction(**settings(on_validate=Mock(side_effect=RuntimeError("authority")))) as cache:
            cache.get_or_convert(Mock(side_effect=AssertionError("must not convert")))
    assert len(rows(Reference)) == 1


def test_bounded_wait_validates_source_and_never_takes_live_lease(store):
    owner = CachedDocumentExtraction(**settings())
    assert owner._claim() == (None, True)
    validate = Mock()
    with pytest.raises(ExtractionCacheUnavailable, match="pending"):
        with cached_document_extraction(**settings(timeout_seconds=0.15, on_validate=validate)) as waiter:
            waiter.get_or_convert(Mock(side_effect=AssertionError("live lease")))
    assert validate.call_count >= 4
    assert rows(Cache)[0].lease_token == owner.token
    owner._close()
    assert rows(Cache) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"content_hash": "bad"},
        {"vision_config": {"api_key": "secret"}},
        {"vision_config": {"binding_uid": "wrong"}},
        {"timeout_seconds": float("inf")},
    ],
)
def test_invalid_identity_rejected(changes):
    with pytest.raises(ValueError):
        CachedDocumentExtraction(**settings(**changes))


def test_migration_upgrade_and_populated_downgrade_preserve_data(engine_store):
    path = Path(__file__).resolve().parents[5] / "api/langboard/migrations/versions/20261022190000-6137cd85bf09.py"
    spec = spec_from_file_location("extraction_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = engine_store
    with engine.begin() as connection:
        schema = engine.get_execution_options().get("schema_translate_map", {}).get(None)
        if schema:
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            Cache.__table__.insert().values(
                id=1,
                created_at=SafeDateTime.now(),
                updated_at=SafeDateTime.now(),
                cache_key="a" * 64,
                keywords={},
                markdown="preserved",
            )
        )
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        assert connection.execute(select(Cache.markdown)).scalar_one() == "preserved"
        connection.execute(Cache.__table__.delete())
        migration.downgrade()


def test_expired_claim_is_taken_over_and_missing_tokens_reject_activation(store):
    owner = CachedDocumentExtraction(**settings())
    owner._claim()
    with DbSession.atomic() as db:
        assert not activate_extraction_reference(
            db, owner.token, source_type="card_attachment", source_uid="source", generation="a" * 32
        )
        assert not activate_extraction_reference(
            db, "c" * 32, source_type="card_attachment", source_uid="source", generation="a" * 32
        )
        db.exec(update(Cache).values(lease_expires_at=SafeDateTime.now() - timedelta(seconds=1)))
    with cached_document_extraction(**settings()) as successor:
        assert successor.get_or_convert(converted)["markdown"] == "private result"
        activate(successor)
    owner._close()
    assert len(rows(Cache)) == len(rows(Reference)) == 1


def test_activated_reference_survives_later_error_without_retain(store):
    with pytest.raises(RuntimeError, match="unrelated"):
        with cached_document_extraction(**settings()) as cache:
            cache.get_or_convert(converted)
            activate(cache)
            raise RuntimeError("unrelated embedding error")
    assert len(rows(Cache)) == len(rows(Reference)) == 1
    assert rows(Reference)[0].expires_at is None


def test_due_cleanup_is_bounded_and_preserves_active_reference(store):
    for value in range(3):
        with cached_document_extraction(**settings(content_hash=sha256(str(value).encode()).hexdigest())) as cache:
            cache.get_or_convert(converted)
            cache.retain()
    with DbSession.atomic() as db:
        db.exec(update(Reference).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
    assert recover_expired_extraction_references(limit=1) == 1
    assert len(rows(Cache)) == len(rows(Reference)) == 2
    with pytest.raises(ValueError):
        recover_expired_extraction_references(limit=101)


@pytest.mark.parametrize(
    "config",
    [
        {"model": {"api_key": "secret"}},
        {"base_url": "https://user:secret@provider.invalid"},
        {"base_url": "https://provider.invalid?api_key=secret"},
        {"top_p": float("nan")},
    ],
)
def test_credentials_and_nested_snapshot_values_reject(config):
    with pytest.raises(ValueError):
        CachedDocumentExtraction(**settings(vision_config=config))


def test_cached_malformed_structure_cannot_reach_consumer(store):
    with cached_document_extraction(**settings()) as cache:
        cache.get_or_convert(converted)
        cache.retain()
    with DbSession.atomic() as db:
        db.exec(update(Cache).values(document_json={"schema_name": "wrong"}))
    with pytest.raises(ValueError, match="structure"):
        with cached_document_extraction(**settings()) as cache:
            cache.get_or_convert(Mock(side_effect=AssertionError("cache hit must not convert")))


def test_opposing_source_cache_swaps_use_global_lock_order(store, monkeypatch):
    """Two native source transactions exchange cache ownership without deadlock."""
    from threading import Barrier
    from time import sleep

    source_settings = [settings(content_hash=sha256(value).hexdigest()) for value in (b"a", b"b")]
    for number, config in enumerate(source_settings):
        with cached_document_extraction(**config) as cache:
            cache.get_or_convert(converted)
            activate(cache, source=f"source{number}")
    tokens = []
    for config in reversed(source_settings):
        with cached_document_extraction(**config) as cache:
            cache.get_or_convert(converted)
            cache.retain()
            tokens.append(cache.token)
    barrier = Barrier(2)
    state = local()
    original = module._lock_entry

    def observe_lock(db, identifier):
        result = original(db, identifier)
        state.calls = getattr(state, "calls", 0) + 1
        if state.calls == 1:
            # Expose opposite acquisition order on PostgreSQL's real row locks.
            sleep(0.05)
        return result

    monkeypatch.setattr(module, "_lock_entry", observe_lock)

    def swap(number):
        barrier.wait(timeout=5)
        with DbSession.atomic() as db:
            assert activate_extraction_reference(
                db, tokens[number], source_type="card_attachment", source_uid=f"source{number}", generation="b" * 32
            )

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(swap, number) for number in range(2)]
        for future in futures:
            future.result(15)
    references = rows(Reference)
    assert len(references) == 2
    assert {ref.generation for ref in references} == {"b" * 32}
    assert all(ref.expires_at is None for ref in references)
    assert len(rows(Cache)) == 2
