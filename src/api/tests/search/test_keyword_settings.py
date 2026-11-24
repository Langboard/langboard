"""Native persisted destination grants; no remote Elasticsearch dependency."""

import importlib.util
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import KeyVault
from langboard_shared.domain.models import (
    DocumentKeywordIndex,
    KeywordSearchConfiguration,
    KeywordSearchConnection,
)
from langboard_shared.domain.services import KeywordSearchSettings as settings
from langboard_shared.domain.services.factory.SecretReferenceService import (
    SecretReferenceConflict,
    SecretReferenceUnavailable,
)
from pydantic import SecretStr


@pytest.fixture
def native(secrets):
    service, board, path = secrets
    engine = DbEngine.get_main_engine()
    DocumentKeywordIndex.__table__.create(engine)
    for model in (KeywordSearchConnection, KeywordSearchConfiguration):
        model.__table__.create(engine)
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    return service, actor, board


def save(
    native,
    *,
    endpoint="https://search.example.invalid",
    ref=None,
    revision=0,
    secret_revision=0,
    reads=True,
    indexing=True,
):
    service, actor, _ = native
    if ref is None:
        ref = service.create(actor, "instance", "instance", "keyword/" + uuid4().hex, SecretStr("fixture-key"))["uri"]
    return settings.save(
        actor,
        type("Host", (), {"secret_reference": service})(),
        expected_revision=revision,
        endpoint=endpoint,
        index="documents",
        secret_ref=ref,
        secret_revision=secret_revision,
        reads_enabled=reads,
        indexing_enabled=indexing,
    )


def test_persistence_instance_boundary_and_current_admin(native):
    service, actor, board = native
    personal = service.create(actor, "personal", "me", "personal/key", SecretStr("personal-key"))
    with pytest.raises(SecretReferenceUnavailable):
        save(native, ref=personal["uri"])
    with pytest.raises(SecretReferenceUnavailable):
        service.move(actor, personal["uri"], "instance", "instance", 0)
    result = save(native)
    assert result["mode"] == "native" and result["revision"] == 1
    assert settings.snapshot(actor) == result
    assert settings.configuration(operation="read")["endpoint"] == "https://search.example.invalid"
    with pytest.raises(SecretReferenceConflict):
        save(native, ref=result["connections"][0]["secret_ref"])
    with DbSession.use(readonly=False) as db:
        actor.is_admin = False
        db.update(actor)
    with pytest.raises(SecretReferenceUnavailable):
        settings.snapshot(actor)
    with pytest.raises(SecretReferenceUnavailable):
        service.get_metadata(actor, result["connections"][0]["secret_ref"])


def test_workload_identity_rotation_revocation_and_retained_cleanup(native):
    service, actor, _ = native
    first = save(native)
    connection = settings.configuration(operation="index")
    assert (
        service.resolve_for_keyword_workload(connection["connection_id"], operation="index").get_secret_value()
        == "fixture-key"
    )
    history = service.list_audit(actor, first["connections"][0]["secret_ref"])
    assert history["items"][0]["actor_uid"] is None
    assert history["items"][0]["actor_kind"] == "workload"
    assert history["items"][0]["principal_id"] == "document_keyword"
    assert history["items"][-1]["actor_uid"] == actor.get_uid()
    second = save(native, endpoint="https://second.example.invalid", revision=1)
    with pytest.raises(ValueError):
        service.resolve_for_keyword_workload(connection["connection_id"], operation="index")
    assert (
        service.resolve_for_keyword_workload(connection["connection_id"], operation="remove").get_secret_value()
        == "fixture-key"
    )
    ref = first["connections"][0]["secret_ref"]
    service.rotate(actor, ref, SecretStr("rotated-key"), 0)
    assert settings.snapshot(actor)["connections"][-1]["reference_available"] is False
    with pytest.raises(SecretReferenceUnavailable):
        service.resolve_for_keyword_workload(connection["connection_id"], operation="remove")
    settings.revoke_connection(actor, first["selected_connection_uid"], second["revision"])
    with pytest.raises(ValueError):
        service.resolve_for_keyword_workload(connection["connection_id"], operation="remove")


def test_rotation_during_vault_fetch_denies_without_false_audit(native, monkeypatch):
    service, actor, _ = native
    result = save(native)
    connection = settings.configuration(operation="index")
    ref = result["connections"][0]["secret_ref"]
    original = KeyVault.provider.get_key

    def revoke_during_read(locator):
        material = original(locator)
        service.revoke(actor, ref, 0)
        return material

    monkeypatch.setattr(KeyVault.provider, "get_key", revoke_during_read)
    with pytest.raises(SecretReferenceUnavailable):
        service.resolve_for_keyword_workload(connection["connection_id"], operation="index")
    assert not any(row["actor_kind"] == "workload" for row in service.list_audit(actor, ref)["items"])


def test_switch_preserves_jobs_and_revocation_retains_evidence(native):
    first = save(native)
    config = settings.configuration(operation="index")
    with DbSession.use(readonly=False) as db:
        job = DocumentKeywordIndex(
            attachment_id=101,
            card_id=102,
            project_id=103,
            destination_fingerprint="fixture",
            destination={"endpoint": config["endpoint"], "index": config["index"]},
            source_generation="generation",
            connection_id=config["connection_id"],
            version=7,
            completed_version=7,
        )
        db.insert(job)
    second = save(native, endpoint="https://second.example.invalid", revision=1)
    with DbSession.use(readonly=False) as db:
        retained = db.exec(SqlBuilder.select.table(DocumentKeywordIndex)).first()
        assert retained.version == 7 and retained.operation == "index" and retained.completed_version == 7
    settings.revoke_connection(native[1], first["selected_connection_uid"], second["revision"])
    with DbSession.use(readonly=False) as db:
        retained = db.exec(SqlBuilder.select.table(DocumentKeywordIndex)).first()
        assert retained.last_error == "original_connection_revoked" and retained.version == 8


def test_keyword_settings_migration_retains_legacy_and_refuses_populated_downgrade(secrets, monkeypatch):
    engine = DbEngine.get_main_engine()
    # Historical queue schema has no native connection identity.
    versions = Path(__file__).resolve().parents[2] / "langboard/migrations/versions"
    for filename in ("20261019010000-d95e63b0f721.py", "20261019020000-e06f74c10832.py"):
        spec = importlib.util.spec_from_file_location("keyword_migration", versions / filename)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as conn:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()
    with DbSession.use(readonly=False) as db:
        db.insert(KeywordSearchConfiguration())
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()


def test_http_admin_guard_revision_restart_and_no_plaintext(native, monkeypatch):
    import importlib
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.settings.KeywordSearchSettingsApi import update_keyword_settings
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    service, actor, _ = native
    ref = service.create(actor, "instance", "instance", "keyword/http", SecretStr("fixture-key"))
    wrapper = SimpleNamespace(secret_reference=service, close=lambda: None)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: wrapper
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is update_keyword_settings:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: wrapper
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as client:
        assert client.get("/settings/keyword-search").status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        form = dict(
            expected_revision=0,
            endpoint="https://search.example.invalid",
            index="documents",
            secret_ref=ref["uri"],
            secret_revision=0,
            reads_enabled=True,
            indexing_enabled=True,
        )
        response = client.put("/settings/keyword-search", headers=headers, json=form)
        assert response.status_code == 200, response.text
        assert "fixture-key" not in response.text and response.json()["revision"] == 1
        assert client.put("/settings/keyword-search", headers=headers, json=form).status_code == 409
        assert client.get("/settings/keyword-search", headers=headers).json()["revision"] == 1
        with DbSession.use(readonly=False) as db:
            actor.is_admin = False
            db.update(actor)
        assert client.get("/settings/keyword-search", headers=headers).status_code in {401, 403}


def test_late_worker_completion_after_switch_retains_pending_job(native, monkeypatch):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.tasks.docling import DocumentKeywordTask as keyword

    save(native)
    config = settings.configuration(operation="index")
    from langboard_shared.domain.services import DomainService

    monkeypatch.setattr(DomainService, "secret_reference", property(lambda _: native[0]))
    with DbSession.use(readonly=False) as db:
        job = DocumentKeywordIndex(
            attachment_id=101,
            card_id=102,
            project_id=103,
            destination_fingerprint=keyword.fingerprint({key: config[key] for key in ("endpoint", "index")}),
            destination={key: config[key] for key in ("endpoint", "index")},
            source_generation="generation",
            connection_id=config["connection_id"],
            due_at=SafeDateTime.now(),
        )
        db.insert(job)
    monkeypatch.setattr(keyword, "_payload", lambda _: {"active": True})

    def switch_during_send(job, payload):
        assert not DbSession.has_active_transaction()
        save(native, endpoint="https://second.example.invalid", revision=1)

    monkeypatch.setattr(keyword, "_send", switch_during_send)
    assert keyword.process_keyword(int(job.id)) is False
    with DbSession.use(readonly=False) as db:
        current = db.exec(
            SqlBuilder.select.table(DocumentKeywordIndex).where(DocumentKeywordIndex.id == job.id)
        ).first()
        assert current.completed_version == 0 and current.due_at is not None
        assert current.last_error == "keyword_index_failed_check_original_connection"
    assert settings.snapshot(native[1])["connections"][-1]["blocked_index_jobs"] == 1


def test_instance_reference_cannot_grant_generic_user_or_inbound_app_runtime(native):
    from langboard_shared.domain.services.factory.SecretReferenceService import SecretAuditSource

    service, actor, _ = native
    result = save(native)
    uri = result["connections"][0]["secret_ref"]
    with pytest.raises(SecretReferenceUnavailable):
        service.resolve_for_runtime(actor, uri)
    with pytest.raises(SecretReferenceUnavailable):
        with DbSession.atomic():
            service.audit_binding(actor, uri, 0, source=SecretAuditSource("app_connection", "fixture"))


def test_legacy_jobs_never_acquire_native_key_and_cleanup_requires_original_env(native, monkeypatch):
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", "https://legacy.example.invalid")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX", "legacy")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "true")
    monkeypatch.setenv("LANGBOARD_ELASTICSEARCH_API_KEY", "legacy-key")
    legacy = settings.configuration(operation="index")
    assert legacy["legacy"] and legacy["connection_id"] is None
    save(native)
    with pytest.raises(ValueError, match="Legacy indexing"):
        settings.runtime_options(legacy, operation="index")
    assert settings.runtime_options(legacy, operation="remove")["api_key"] == "legacy-key"
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", "https://other.example.invalid")
    with pytest.raises(ValueError, match="Original keyword connection"):
        settings.runtime_options(legacy, operation="remove")


def test_settings_page_counts_are_bounded_without_hydrating_document_rows(native):
    save(native)
    with DbSession.use(readonly=False) as db:
        for i in range(53):
            db.insert(
                KeywordSearchConnection(
                    endpoint=f"https://target{i}.example.invalid",
                    index="documents",
                    secret_ref="secret://ref/fixture",
                    secret_revision=0,
                )
            )
    first = settings.snapshot(native[1])
    second = settings.snapshot(native[1], cursor=first["next_cursor"])
    assert len(first["connections"]) == 50 and len(second["connections"]) == 4
    assert second["next_cursor"] is None
    assert first["selected_connection"]["endpoint"] == "https://search.example.invalid"
    assert not any(item["uid"] == first["selected_connection_uid"] for item in first["connections"])
    assert not set(item["uid"] for item in first["connections"]) & set(item["uid"] for item in second["connections"])


def test_reapproval_preserves_old_selected_connection_beyond_first_page(native):
    first = save(native)
    with DbSession.use(readonly=False) as db:
        for i in range(51):
            db.insert(KeywordSearchConnection(endpoint=f"https://new{i}.example.invalid", index="documents",
                secret_ref="secret://ref/fixture", secret_revision=0))
    response = settings.snapshot(native[1])
    selected = response["selected_connection"]
    assert selected["uid"] == first["selected_connection_uid"]
    updated = save(native, ref=selected["secret_ref"], revision=response["revision"])
    assert updated["selected_connection_uid"] == selected["uid"]


def test_switch_at_final_ack_boundary_is_rechecked_under_transaction(native, monkeypatch):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.tasks.docling import DocumentKeywordTask as keyword
    save(native)
    config = settings.configuration(operation="index")
    with DbSession.use(readonly=False) as db:
        job = DocumentKeywordIndex(attachment_id=101, card_id=102, project_id=103,
            destination_fingerprint="fixture", destination={"endpoint": config["endpoint"], "index": config["index"]},
            source_generation="generation", connection_id=config["connection_id"], due_at=SafeDateTime.now())
        db.insert(job)
    sent = False
    switched = False
    original_atomic = DbSession.atomic
    def atomic_at_ack(*args, **kwargs):
        nonlocal switched
        if sent and not switched:
            switched = True
            save(native, endpoint="https://second.example.invalid", revision=1)
        return original_atomic(*args, **kwargs)
    monkeypatch.setattr(DbSession, "atomic", atomic_at_ack)
    monkeypatch.setattr(keyword, "_payload", lambda _: {"active": True})
    def send(job, payload):
        nonlocal sent
        assert not DbSession.has_active_transaction()
        sent = True
    monkeypatch.setattr(keyword, "_send", send)
    original_config = settings.configuration
    locked_rechecks = []
    def checked_configuration(**kwargs):
        if kwargs.get("lock"):
            locked_rechecks.append(DbSession.has_active_transaction())
        return original_config(**kwargs)
    monkeypatch.setattr(keyword, "configuration", checked_configuration)
    assert keyword.process_keyword(int(job.id)) is False
    assert switched and locked_rechecks == [True]
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(DocumentKeywordIndex).where(DocumentKeywordIndex.id == job.id)).first()
        assert current.completed_version == 0 and current.due_at is not None
