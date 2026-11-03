"""Protected names retain private forward history across material retirement."""

import json
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security import KeyVault
from langboard_shared.core.security.vault.LocalDevVaultProvider import LocalDevVaultProvider
from langboard_shared.domain.models import SecretReference, SecretReferenceAudit
from langboard_shared.domain.services.factory.SecretReferenceService import SecretReferenceUnavailable
from pydantic import SecretStr
from src.api.tests.secrets import test_wiki_selection_secret as wiki_selection


conversion = wiki_selection.conversion


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_retired_selected_plaintext_cannot_be_republished(conversion, monkeypatch):
    service, board, _, _, _, _, material, _ = conversion
    monkeypatch.setattr(KeyVault.provider, "get_key", material.__getitem__, raising=False)
    reference = wiki_selection.call(conversion)["reference"]
    service.rotate(board[1], reference["uri"], SecretStr("replacement-sensitive-value"), 0)
    assert "fixture-sensitive-value" not in material.values()
    with pytest.raises(ValueError, match="Invalid logical secret name"):
        service.rename(board[1], reference["uri"], "wiki/fixture-sensitive-value", 1)
    assert "fixture-sensitive-value" not in json.dumps(service.get_metadata(board[1], reference["uri"]))
    row = reference_row()
    history = material[row.protection_locator]
    ordinary = json.dumps([reference, row.model_dump(mode="json"), service.list_audit(board[1], reference["uri"])])
    ordinary += repr(row) + repr(conversion[7])
    assert row.protection_locator not in ordinary
    assert history not in ordinary and json.loads(history)["key"] not in ordinary


def reference_row():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(SecretReference)).first()


def vault_files(provider):
    return {path.name: path.read_text() for path in provider.base_dir.iterdir() if path.is_file()}


def assert_history_denied(service, actor, uri, revision, material="old-material"):
    for operation in (service.rename, service.copy):
        with pytest.raises(ValueError, match="Invalid logical secret name"):
            operation(actor, uri, "safe/" + material + "/suffix", revision)


def test_rotation_copy_lineage_and_private_projection(secrets):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    before = reference_row()
    old_locator, old_guard = before.locator, before.protection_locator
    initial_guard = KeyVault.provider.get_key(old_guard)
    service.rotate(actor, original["uri"], SecretStr("current-material"), 0)
    assert_history_denied(service, actor, original["uri"], 1)
    row = reference_row()
    assert old_locator not in vault_files(KeyVault.provider)
    assert old_guard not in vault_files(KeyVault.provider)
    state = KeyVault.provider.get_key(row.protection_locator)
    assert "old-material" not in state and "current-material" not in state
    assert len(json.loads(state)["entries"]) == 2
    copied = service.copy(actor, original["uri"], "safe/copied", 1)
    assert_history_denied(service, actor, copied["uri"], 0)
    renamed = service.rename(actor, copied["uri"], "safe/ordinary", 0)
    assert renamed["revision"] == 1
    assert service.resolve_for_runtime(actor, copied["uri"]).get_secret_value() == "current-material"
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(SecretReference)).all()
        audits = db.exec(SqlBuilder.select.table(SecretReferenceAudit)).all()
    public = json.dumps([r.model_dump(mode="json") for r in rows] + [a.model_dump(mode="json") for a in audits])
    public += repr(rows) + json.dumps(service.list_audit(actor, copied["uri"]))
    for protected in ("old-material", "current-material", state, initial_guard, json.loads(state)["key"]):
        assert protected not in public
    for r in rows:
        assert r.protection_locator not in public
        assert r.notification_data() == {}
    assert len({r.protection_locator for r in rows}) == 2
    service.revoke(actor, original["uri"], 1)
    assert row.protection_locator not in vault_files(KeyVault.provider)
    assert_history_denied(service, actor, copied["uri"], 1)
    with pytest.raises(SecretReferenceUnavailable):
        service.rename(actor, original["uri"], "safe/old-material", 2)


@pytest.mark.parametrize("operation", ["rotate", "copy", "revoke", "create"])
def test_audit_rollback_preserves_state_and_removes_new_objects(secrets, monkeypatch, operation):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    before = vault_files(KeyVault.provider)
    old_guard = reference_row().protection_locator

    def failure(*args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service, "_audit", failure)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        if operation == "rotate":
            service.rotate(actor, original["uri"], SecretStr("new-material"), 0)
        elif operation == "copy":
            service.copy(actor, original["uri"], "safe/copy", 0)
        elif operation == "revoke":
            service.revoke(actor, original["uri"], 0)
        else:
            service.create(actor, "personal", "me", "safe/new", SecretStr("new-material"))
    assert vault_files(KeyVault.provider) == before
    assert reference_row().protection_locator == old_guard
    assert reference_row().revision == 0


@pytest.mark.parametrize("corruption", ["missing", "json", "shape", "missing-current"])
@pytest.mark.parametrize("operation", ["rename", "copy", "rotate"])
def test_unavailable_or_corrupt_guard_fails_closed(secrets, corruption, operation):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    row = reference_row()
    if corruption == "missing":
        KeyVault.provider.delete_key(row.protection_locator)
    else:
        value = {
            "json": "not-json",
            "shape": "{}",
            "missing-current": json.dumps({"version": 1, "key": "a" * 64, "entries": [[1, "b" * 64]]}),
        }[corruption]
        KeyVault.provider.store_secret(row.protection_locator, value)
    before = vault_files(KeyVault.provider)
    with pytest.raises(SecretReferenceUnavailable):
        if operation == "rotate":
            service.rotate(actor, original["uri"], SecretStr("new-material"), 0)
        else:
            getattr(service, operation)(actor, original["uri"], "safe/new", 0)
    assert vault_files(KeyVault.provider) == before
    assert reference_row().revision == 0


@pytest.mark.parametrize("operation", ["rotate", "copy", "migrate"])
def test_legacy_seeds_only_still_available_material(secrets, monkeypatch, tmp_path, operation):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    source = KeyVault.provider
    with DbSession.use(readonly=False) as db:
        row = db.exec(SqlBuilder.select.table(SecretReference)).first()
        source.delete_key(row.protection_locator)
        row.protection_locator = None
        db.update(row)
    if operation == "rotate":
        service.rotate(actor, original["uri"], SecretStr("new-material"), 0)
        target_uri, revision = original["uri"], 1
    elif operation == "copy":
        copied = service.copy(actor, original["uri"], "safe/copy", 0)
        target_uri, revision = copied["uri"], 0
    else:

        class Target(LocalDevVaultProvider):
            def name(self):
                return "target"

        monkeypatch.setattr(KeyVault, "provider", Target(tmp_path / "target"))
        service.migrate_provider(actor, original["uri"], 0, source)
        target_uri, revision = original["uri"], 1
    assert_history_denied(service, actor, target_uri, revision)
    # A value retired before this implementation has no available comparison
    # source. This assertion records the boundary, not historical backfill.
    allowed = service.rename(actor, target_uri, "safe/unrecoverable-prior-material", revision)
    assert allowed["revision"] == revision + 1


@pytest.mark.parametrize("failure", [None, "audit", "guard-write", "verify", "guard-read", "material-read"])
def test_provider_migration_preserves_lineage_and_compensates(secrets, monkeypatch, tmp_path, failure, caplog):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    service.rotate(actor, original["uri"], SecretStr("new-material"), 0)
    source = KeyVault.provider
    source_before = vault_files(source)

    class Target(LocalDevVaultProvider):
        def name(self):
            return "target"

    target = Target(tmp_path / "target")
    monkeypatch.setattr(KeyVault, "provider", target)
    if failure == "audit":
        monkeypatch.setattr(service, "_audit", lambda *args: (_ for _ in ()).throw(RuntimeError("audit unavailable")))
    elif failure == "guard-write":
        monkeypatch.setattr(
            target, "store_secret", lambda *args: (_ for _ in ()).throw(RuntimeError("storage unavailable"))
        )
    elif failure == "verify":
        monkeypatch.setattr(target, "get_key", lambda *args: "invalid")
    elif failure in {"guard-read", "material-read"}:
        native_get = target.get_key

        def get(locator):
            value = native_get(locator)
            if value.startswith('{"version":') == (failure == "guard-read"):
                raise RuntimeError("private-provider-data:" + locator + value)
            return value

        monkeypatch.setattr(target, "get_key", get)
    if failure:
        with pytest.raises((RuntimeError, SecretReferenceUnavailable)) as error:
            service.migrate_provider(actor, original["uri"], 1, source)
        assert "private-provider-data:" not in str(error.value) + caplog.text
        assert vault_files(source) == source_before
        assert vault_files(target) == {}
        assert reference_row().revision == 1
    else:
        service.migrate_provider(actor, original["uri"], 1, source)
        assert vault_files(source) == {}
        assert_history_denied(service, actor, original["uri"], 2)
        assert service.resolve_for_runtime(actor, original["uri"]).get_secret_value() == "new-material"
        guard = reference_row().protection_locator
        service.revoke(actor, original["uri"], 2)
        assert guard not in vault_files(target)


def test_after_commit_cleanup_failure_keeps_new_guard(secrets, monkeypatch, caplog):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    old = reference_row()
    old_locator = old.locator
    native_delete = KeyVault.provider.delete_key

    def delete(locator):
        if locator == old_locator:
            raise RuntimeError("cleanup unavailable")
        native_delete(locator)

    monkeypatch.setattr(KeyVault.provider, "delete_key", delete)
    service.rotate(actor, original["uri"], SecretStr("new-material"), 0)
    assert reference_row().revision == 1
    assert_history_denied(service, actor, original["uri"], 1)
    assert "Sensitive after-commit callback failed" in caplog.text
    assert old.protection_locator not in vault_files(KeyVault.provider)


@pytest.mark.parametrize("operation", ["create", "copy", "rotate"])
@pytest.mark.parametrize("write_before_failure", [False, True])
def test_guard_storage_failure_compensates_new_material_without_logging_state(
    secrets, monkeypatch, caplog, operation, write_before_failure
):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("old-material"))
    provider = KeyVault.provider
    before = vault_files(provider)
    native_store = provider.store_secret
    attempted = []

    def store(locator, value):
        if value.startswith('{"version":'):
            attempted.append(value)
            if write_before_failure:
                native_store(locator, value)
            raise RuntimeError(value)
        return native_store(locator, value)

    monkeypatch.setattr(provider, "store_secret", store)
    with pytest.raises(SecretReferenceUnavailable) as error:
        if operation == "create":
            service.create(actor, "personal", "me", "safe/new", SecretStr("new-material"))
        elif operation == "copy":
            service.copy(actor, original["uri"], "safe/copy", 0)
        else:
            service.rotate(actor, original["uri"], SecretStr("new-material"), 0)
    assert vault_files(provider) == before
    assert attempted and attempted[0] not in caplog.text + str(error.value)
    assert reference_row().revision == 0


def test_multiple_rotations_keep_every_known_material(secrets):
    service, board, _ = secrets
    actor = board[1]
    original = service.create(actor, "personal", "me", "safe/source", SecretStr("material-0"))
    for revision in range(6):
        service.rotate(actor, original["uri"], SecretStr(f"material-{revision + 1}"), revision)
    history = json.loads(KeyVault.provider.get_key(reference_row().protection_locator))
    assert len(history["entries"]) == 7
    for revision in range(7):
        assert_history_denied(service, actor, original["uri"], 6, f"material-{revision}")


def test_revoke_wrong_configured_provider_keeps_private_source_locator(secrets, monkeypatch):
    service, board, _ = secrets
    original = service.create(board[1], "personal", "me", "safe/source", SecretStr("old-material"))
    guard = reference_row().protection_locator
    source = KeyVault.provider
    from types import SimpleNamespace

    monkeypatch.setattr(KeyVault, "provider", SimpleNamespace(name=lambda: "unavailable-provider"))
    service.revoke(board[1], original["uri"], 0)
    assert reference_row().protection_locator == guard
    assert source.get_key(guard)
    with pytest.raises(SecretReferenceUnavailable):
        service.rename(board[1], original["uri"], "safe/old-material", 1)
