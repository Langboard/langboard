# ruff: noqa: F811
"""Grouping is explicit presentation metadata, with no module authority migration."""

from copy import deepcopy
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AppComposition, AppCompositionMember, AppDefinition, BoardAppBinding
from langboard_shared.domain.services.AppComposition import (
    board_compositions,
    disable_board_composition,
    list_compositions,
    save_composition,
    ungroup_composition,
)
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict, AppRegistryDenied
from test_app_card_creation import creation  # noqa: F401


@pytest.fixture
def composition(board, monkeypatch):
    from langboard_shared.publishers import AppSettingPublisher
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    for model in (AppComposition, AppCompositionMember):
        model.__table__.create(DbEngine.get_main_engine())
    actor = board[1]
    declaration = {
        "schema_version": 1, "key": "independent-panel", "version": "1.0.0", "name": "Independent",
        "description": "Independent module, no provider name heuristic.", "capabilities": ["panels.render"],
        "resource_types": [], "panel": {"url": "https://example.invalid/panel", "name": "Panel", "icon": "x"},
    }
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
        db.insert(AppDefinition(key="independent-panel", declaration=declaration, approved_by=actor.id))
        db.insert(BoardAppBinding(project_id=board[2].id, app_key="github", state="enabled",
                                 granted_capabilities=["signals.read"], stage_transitions_enabled=False))
    return board


def save(actor, **kwargs):
    return save_composition(actor, "review-tools", "Review tools", "Explicit module grouping.",
                            kwargs.pop("module_keys", ["github", "independent-panel"]), **kwargs)


def test_group_update_ungroup_preserves_exact_authority_records(composition):
    board = composition
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == "github")).first()
        before = (binding.id, binding.edit_revision(), deepcopy(binding.granted_capabilities))
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        definition_before = (definition.id, definition.edit_revision(), deepcopy(definition.declaration))
    saved = save(actor)
    assert saved["module_keys"] == ["github", "independent-panel"]
    assert saved["generation"] == 1
    assert save(actor, expected_revision=saved["revision"]) == saved
    grouped = board_compositions(board[0], actor, board[2].get_uid())
    assert grouped["compositions"] == [saved]
    assert {app["key"] for app in grouped["apps"]} == {"github", "glitchtip", "dokploy", "independent-panel"}
    assert next(app for app in grouped["apps"] if app["key"] == "github")["binding"]["granted_capabilities"] == ["signals.read"]
    assert next(app for app in grouped["apps"] if app["key"] == "independent-panel")["binding"] is None
    with pytest.raises(AppRegistryConflict):
        save(actor)
    changed = save(actor, module_keys=["independent-panel", "github"], expected_revision=saved["revision"])
    assert changed["generation"] == 2 and changed["revision"] != saved["revision"]
    with pytest.raises(AppRegistryConflict):
        ungroup_composition(actor, "review-tools", saved["revision"])
    assert ungroup_composition(actor, "review-tools", changed["revision"])["ungrouped"]
    assert list_compositions(actor) == []
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == "github")).first()
        assert (binding.id, binding.edit_revision(), binding.granted_capabilities) == before
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        assert (definition.id, definition.edit_revision(), definition.declaration) == definition_before
    assert save(actor)["generation"] == 1


def test_competing_membership_and_invalid_targets_rollback(composition):
    actor = composition[1]
    saved = save(actor)
    with pytest.raises(AppRegistryConflict):
        save_composition(actor, "other", "Other", "Competing grouping.", ["github"])
    assert list_compositions(actor) == [saved]
    for keys in (["review-tools"], ["github-panel"], ["github", "github"], []):
        with pytest.raises(ValueError):
            save(actor, module_keys=keys, expected_revision=saved["revision"])
    assert list_compositions(actor) == [saved]


def test_full_disable_is_atomic_and_revocation_survives_unavailable_definition(composition):
    service, actor, project = composition[:3]
    saved = save(actor)
    with DbSession.use(readonly=False) as db:
        second = db.exec(
            SqlBuilder.select.table(AppDefinition)).first()
        second.is_enabled = False
        db.update(second)
        db.insert(BoardAppBinding(project_id=project.id, app_key="independent-panel", state="enabled",
                                 granted_capabilities=["panels.render"], stage_transitions_enabled=True))
    snapshot = board_compositions(service, actor, project.get_uid())
    targets = {app["key"]: app["binding"]["revision"] for app in snapshot["apps"] if app["key"] in saved["module_keys"]}
    for revisions in ({**targets, "independent-panel": "0" * 64}, {"github": targets["github"]}):
        with pytest.raises(AppRegistryConflict):
            disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], revisions)
        current = board_compositions(service, actor, project.get_uid())
        assert all(app["binding"]["state"] == "enabled" for app in current["apps"] if app["key"] in targets)
    result = disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], targets)
    assert result["disabled"] and result["module_keys"] == saved["module_keys"]
    current = board_compositions(service, actor, project.get_uid())
    updated = {app["key"]: app["binding"]["revision"] for app in current["apps"] if app["key"] in targets}
    assert all(app["binding"]["state"] == "disabled" and app["binding"]["granted_capabilities"] == []
               and not app["binding"]["stage_transitions_enabled"] for app in current["apps"] if app["key"] in targets)
    assert disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], updated) == result
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(BoardAppBinding)).all()) == 2
        assert db.exec(SqlBuilder.select.table(AppDefinition)).first().is_enabled is False
        actor.is_admin = False
        db.update(actor)
        db.delete(composition[3])
    with pytest.raises(AppRegistryDenied):
        disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], updated)


def test_full_disable_rejects_new_binding_or_changed_membership(composition):
    service, actor, project = composition[:3]
    saved = save(actor)
    snapshot = board_compositions(service, actor, project.get_uid())
    targets = {app["key"]: app["binding"]["revision"] if app["binding"] else None
               for app in snapshot["apps"] if app["key"] in saved["module_keys"]}
    with DbSession.use(readonly=False) as db:
        db.insert(BoardAppBinding(project_id=project.id, app_key="independent-panel", state="enabled",
                                 granted_capabilities=["panels.render"]))
    with pytest.raises(AppRegistryConflict):
        disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], targets)
    changed = save(actor, module_keys=["github"], expected_revision=saved["revision"])
    with pytest.raises(AppRegistryConflict):
        disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], targets)
    assert disable_board_composition(service, actor, project.get_uid(), saved["key"], changed["revision"],
                                     {"github": targets["github"]})["disabled"]


def test_current_admin_and_board_read_authority_are_required(composition):
    actor = composition[1]
    saved = save(actor)
    with DbSession.use(readonly=False) as db:
        actor.is_admin = False
        db.update(actor)
    for action in (lambda: list_compositions(actor), lambda: save(actor, expected_revision=saved["revision"]),
                   lambda: ungroup_composition(actor, "review-tools", saved["revision"])):
        with pytest.raises(AppRegistryDenied):
            action()
    assert board_compositions(composition[0], actor, composition[2].get_uid())["compositions"] == [saved]
    with DbSession.use(readonly=False) as db:
        db.delete(composition[3])
    with pytest.raises(AppRegistryDenied):
        board_compositions(composition[0], actor, composition[2].get_uid())


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_authenticated_http_composition_management_and_old_catalog(composition, monkeypatch):
    import langboard.routes.board.BoardSettingApi  # noqa: F401
    import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    board = composition
    # HTTP authentication uses the normal read engine; service tests separately
    # enforce primary-only composition authority reads.
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(board[1].id)
    headers = {"Authorization": f"Bearer {access}"}
    path = "/settings/apps/compositions"
    board_path = f"/board/{board[2].get_uid()}/settings/apps"
    payload = {"key": "review-tools", "name": "Review tools", "description": "Explicit grouping.",
               "module_keys": ["github", "independent-panel"]}
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        before = client.get(board_path, headers=headers).json()
        catalog = client.get(path, headers=headers).json()
        assert catalog["compositions"] == []
        keys = {row["key"] for row in catalog["modules"]}
        assert {"github", "independent-panel"}.issubset(keys)
        assert "github-panel" not in keys
        assert "declaration" not in catalog["modules"][-1]
        saved = client.post(path, headers=headers, json=payload)
        assert saved.status_code == 200
        revision = saved.json()["composition"]["revision"]
        assert client.post(path, headers=headers, json=payload).status_code == 409
        assert client.post(path, headers=headers, json={**payload, "module_keys": ["not-installed"],
                                                       "expected_revision": revision}).status_code == 400
        assert client.get(board_path, headers=headers).json() == before
        grouped = client.get(board_path + "/compositions", headers=headers)
        assert grouped.status_code == 200
        assert grouped.json()["apps"] == before["apps"]
        assert grouped.json()["compositions"][0]["module_keys"] == payload["module_keys"]
        assert client.post(path + "/review-tools/ungroup", headers=headers,
                           json={"expected_revision": "0" * 64}).status_code == 409
        targets = {app["key"]: app["binding"]["revision"] if app["binding"] else None
                   for app in before["apps"] if app["key"] in payload["module_keys"]}
        disable_path = board_path + "/compositions/review-tools/disable"
        assert client.post(disable_path, headers=headers, json={"expected_revision": revision,
            "binding_revisions": {**targets, "github": "0" * 64}}).status_code == 409
        assert client.get(board_path, headers=headers).json() == before
        disabled = client.post(disable_path, headers=headers, json={"expected_revision": revision,
            "binding_revisions": targets})
        assert disabled.status_code == 200 and disabled.json()["disabled"]
        with DbSession.use(readonly=False) as db:
            board[1].is_admin = False
            db.update(board[1])
        assert client.get(path, headers=headers).status_code == 403
        assert client.post(path + "/review-tools/ungroup", headers=headers,
                           json={"expected_revision": revision}).status_code == 403
        assert client.get(board_path + "/compositions", headers=headers).status_code == 200


def test_schema_upgrade_rollback_preserves_module_tables(board, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langboard_shared.publishers import AppSettingPublisher
    from sqlalchemy import inspect
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    engine = DbEngine.get_main_engine()
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261011150000-e71b63c8a204.py"
    spec = importlib.util.spec_from_file_location("composition_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        for model in (AppComposition, AppCompositionMember):
            assert {c["name"] for c in inspect(connection).get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
    with DbSession.use(readonly=False) as db:
        board[1].is_admin = True
        db.update(board[1])
    saved = save_composition(board[1], "native-tools", "Native tools", "Explicit native modules.", ["github", "dokploy"])
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="Ungroup"):
            module.downgrade()
    ungroup_composition(board[1], "native-tools", saved["revision"])
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.downgrade()
        tables = inspect(connection).get_table_names()
        assert "app_composition" not in tables and "app_composition_member" not in tables
        assert "app_definition" in tables and "board_app_binding" in tables


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_group_withdrawal_preserves_native_app_card_signal_and_blocks_pending_delivery(creation, monkeypatch):
    from langboard_shared.domain.models import (
        AppCardArchive,
        AppConnection,
        AppExecutionOutbox,
        AppResourceBinding,
        AppSignal,
        Card,
        CardAppOwnership,
    )
    from langboard_shared.domain.services.AppEventDelivery import claim_app_event
    from test_app_card_archive_delivery import setup_archive
    from test_app_card_creation import call
    card, _, _ = setup_archive(creation, monkeypatch)
    board, _, connection, _, resource, *_ = creation
    service, actor, project = board[:3]
    saved = save_composition(actor, "external-tools", "External tools", "Native and independent modules.",
                             ["github", "example-erp"])
    with DbSession.atomic() as db:
        signal = AppSignal(resource_id=resource.id, provider="example-erp", event_id="preserved-evidence",
                           event_type="issue.updated", occurred_at="2026-10-11T00:00:00Z", external_id="issue-1",
                           outcome="open", commit_sha="", payload_digest="a" * 64)
        db.insert(signal)
        ownership = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id)).first()
        archive = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
        preserved = (card.id, card.title, ownership.id, ownership.app_key, ownership.revision,
                     archive.id, deepcopy(archive.receipt), signal.id, signal.payload_digest,
                     connection.id, resource.id)
    snapshot = board_compositions(service, actor, project.get_uid())
    targets = {app["key"]: app["binding"]["revision"] if app["binding"] else None
               for app in snapshot["apps"] if app["key"] in saved["module_keys"]}
    disable_board_composition(service, actor, project.get_uid(), saved["key"], saved["revision"], targets)
    assert claim_app_event(event.id, 1) is None
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
    with pytest.raises(AppGovernanceDenied):
        call(creation, title="Not allowed after withdrawal")
    with DbSession.atomic() as db:
        current_card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        ownership = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id)).first()
        archive = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        signal = db.exec(SqlBuilder.select.table(AppSignal)).first()
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection.id)).first()
        resource = db.exec(SqlBuilder.select.table(AppResourceBinding).where(AppResourceBinding.id == resource.id)).first()
        assert (current_card.id, current_card.title, ownership.id, ownership.app_key, ownership.revision,
                archive.id, archive.receipt, signal.id, signal.payload_digest, connection.id, resource.id) == preserved
        assert connection.state == "connected" and resource.is_selected
        delivery = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.id == event.id)).first()
        assert delivery.state == "blocked" and delivery.attempt_count == 0


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_grouped_app_update_retains_only_existing_consent_and_preserves_owned_cards(creation, monkeypatch):
    from langboard_shared.domain.models import AppConnection, Card, CardAppOwnership
    from langboard_shared.domain.services.AppRegistry import save_definition
    from langboard_shared.publishers import AppSettingPublisher
    from test_app_card_creation import call
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    board, _, connection, binding, _, definition, _ = creation
    service, actor, project = board[:3]
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
    call(creation)
    group = save_composition(actor, "external-tools", "External tools", "Explicit external module.", ["example-erp"])
    before_revision = binding.edit_revision()
    with DbSession.atomic() as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.title == "ERP issue")).first()
        ownership = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id)).first()
        before = (card.id, card.title, card.description, ownership.id, ownership.app_key, ownership.revision)
    ordinary = {**definition.declaration, "version": "1.1.0",
                "capabilities": [*definition.declaration["capabilities"], "panels.render"]}
    approved = save_definition(actor, ordinary, definition.edit_revision())
    current = board_compositions(service, actor, project.get_uid())
    assert current["compositions"] == [group]
    module = next(app for app in current["apps"] if app["key"] == "example-erp")
    assert "panels.render" not in module["binding"]["granted_capabilities"]
    assert module["binding"]["state"] == "enabled"
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection.id)).first().state == "connected"
    changed = {**ordinary, "version": "1.2.0", "publisher": "Different publisher"}
    save_definition(actor, changed, approved["revision"])
    with pytest.raises(AppRegistryConflict):
        disable_board_composition(service, actor, project.get_uid(), group["key"], group["revision"],
                                  {"example-erp": before_revision})
    current = board_compositions(service, actor, project.get_uid())
    assert current["compositions"] == [group]
    module = next(app for app in current["apps"] if app["key"] == "example-erp")
    assert module["binding"]["state"] == "needs_attention" and module["binding"]["granted_capabilities"] == []
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection.id)).first().state == "revoked"
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        ownership = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id)).first()
        assert (card.id, card.title, card.description, ownership.id, ownership.app_key, ownership.revision) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
async def test_sdk_composition_management_uses_authenticated_native_http(composition, monkeypatch):
    from types import SimpleNamespace
    import langboard.routes.board.BoardSettingApi  # noqa: F401
    import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_sdk import AppManager, AppRegistry, HttpTransport, NativeApiError
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(composition[1].id)
    with TestClient(app) as session:
        session.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        async def request(method, path, **kwargs):
            return session.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)
        transport = HttpTransport(SimpleNamespace(request=request))
        registry = AppRegistry(transport)
        manager = AppManager(transport, composition[2].get_uid())
        catalog = await registry.compositions()
        assert {"github", "independent-panel"}.issubset({module["key"] for module in catalog["modules"]})
        group = await registry.save_composition("tools", "Tools", "Exact installed modules", ["github", "independent-panel"])
        snapshot = await manager.compositions()
        assert snapshot["compositions"] == [group]
        targets = {row["key"]: row["binding"]["revision"] if row["binding"] else None
                   for row in snapshot["apps"] if row["key"] in group["module_keys"]}
        with pytest.raises(NativeApiError) as stale:
            await manager.disable_composition(group["key"], "0" * 64, targets)
        assert stale.value.status_code == 409
        assert (await manager.compositions())["apps"] == snapshot["apps"]
        receipt = await manager.disable_composition(group["key"], group["revision"], targets)
        assert receipt["disabled"] and receipt["module_keys"] == group["module_keys"]
        current = await manager.compositions()
        github = next(row for row in current["apps"] if row["key"] == "github")
        assert github["binding"]["granted_capabilities"] == []
        await registry.ungroup_composition(group["key"], group["revision"])
        assert (await registry.compositions())["compositions"] == []
        assert (await manager.compositions())["apps"] == current["apps"]
