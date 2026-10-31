# ruff: noqa: F811
"""Independent external app registration using authenticated SDK HTTP, no registry patch."""

from types import SimpleNamespace
import langboard.routes.board.AppPanelDataApi  # noqa: F401
import langboard.routes.board.BoardSettingApi  # noqa: F401
import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_sdk import AppManager, AppRegistry, HttpTransport, NativeApiError
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.Env import Env
from test_card_signal_projection import scoped  # noqa: F401
from test_github_signal import installation, lifecycle, signal_storage  # noqa: F401


@pytest.fixture(autouse=True)
def app_notifications(monkeypatch, tmp_path):
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    from langboard_shared.publishers import AppSettingPublisher
    events = []
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: events.append("apps:changed"))
    return events


DECLARATION = {
    "schema_version": 2, "key": "example-erp", "version": "1.0.0", "name": "Example ERP",
    "description": "A separately hosted ERP issue integration.",
    "capabilities": ["cards.create", "cards.presentation"], "resource_types": ["issue"],
    "workflow_requirements": {"required": ["backlog"], "optional": ["active"]},
    "panel": {"url": "https://erp.example.invalid/panel", "name": "ERP outbox", "icon": "📋"},
    "card_lifecycle": {"issue": {"archive": {"mode": "retain", "description": "Retain the external issue history."}, "restore": "retain_external_state"}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http"], indirect=True)
async def test_registration_workflow_update_disable_and_admin_revocation(board, monkeypatch):
    from langboard_shared.domain.models import BoardAppBinding

    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    from langboard_shared.domain.models import (
        AppGovernancePolicy,
        WorkflowRule,
        WorkflowRuleAction,
        WorkflowRuleCondition,
    )
    # Workflow snapshots serialize normalized rules and their child rows.
    for model in (AppGovernancePolicy, WorkflowRule, WorkflowRuleCondition, WorkflowRuleAction):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as session:
        session.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)

        async def request(method, path, **kwargs):
            return session.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)

        transport = HttpTransport(SimpleNamespace(request=request))
        registry = AppRegistry(transport)
        manager = AppManager(transport, board[2].get_uid())
        # Raw requests prove host validation, rather than SDK-only rejection.
        missing = {key: value for key, value in DECLARATION.items() if key != "card_lifecycle"}
        denied = session.post("/settings/apps/registry", json={"declaration": missing}, headers={"Authorization": f"Bearer {access}"})
        assert denied.status_code == 400
        denied = session.post("/settings/apps/registry", json={"declaration": {**missing, "schema_version": 1}}, headers={"Authorization": f"Bearer {access}"})
        assert denied.status_code == 400
        invalid = {**DECLARATION, "card_lifecycle": {"issue": {"archive": {"mode": "retain", "description": "History"}, "restore": "reopen"}}}
        denied = session.post("/settings/apps/registry", json={"declaration": invalid}, headers={"Authorization": f"Bearer {access}"})
        assert denied.status_code == 400
        saved = await registry.approve(DECLARATION)
        assert saved["is_enabled"] and saved["generation"] == 1
        assert (await registry.list())[0]["declaration"] == DECLARATION
        entry = next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")
        assert entry["panel"] == DECLARATION["panel"]
        assert entry["version"] == "1.0.0"
        assert entry["connection_setup_available"] is False
        assert entry["binding"] is None
        draft = await manager.prepare_workflow("example-erp")
        assert next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")["binding"]["granted_capabilities"] == []
        assert not draft["binding"]["stage_transitions_enabled"]
        assert {c["stage"] for c in draft["choices"]} == {"backlog", "active"}
        with pytest.raises(NativeApiError) as duplicate:
            await registry.approve(DECLARATION)
        assert duplicate.value.status_code == 409
        with pytest.raises(NativeApiError) as builtin:
            await registry.approve({**DECLARATION, "key": "github"})
        assert builtin.value.status_code == 400
        with DbSession.use(readonly=False) as db:
            binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == "example-erp")).first()
            binding.state = "enabled"
            binding.granted_capabilities = ["cards.create"]
            binding.stage_transitions_enabled = True
            db.update(binding)
        updated = await registry.approve({**DECLARATION, "version": "1.1.0"}, expected_revision=saved["revision"])
        assert updated["generation"] == 2
        snapshot = next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")["binding"]
        assert snapshot["state"] == "enabled" and snapshot["granted_capabilities"] == ["cards.create"]
        assert not snapshot["stage_transitions_enabled"]
        with pytest.raises(NativeApiError) as stale:
            await registry.disable("example-erp", saved["revision"])
        assert stale.value.status_code == 409
        disabled = await registry.disable("example-erp", updated["revision"])
        assert not disabled["is_enabled"] and disabled["generation"] == 3
        retained = next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")
        assert retained["capabilities"] == [] and retained["app_revision"] == disabled["revision"]
        assert retained["is_available"] is False
        assert retained["binding"]["granted_capabilities"] == []
        with DbSession.use(readonly=False) as db:
            actor.is_admin = False
            db.update(actor)
        with pytest.raises(NativeApiError) as revoked:
            await registry.approve({**DECLARATION, "version": "1.2.0"}, expected_revision=disabled["revision"])
        assert revoked.value.status_code == 403


def test_registry_migration_preserves_approved_definitions():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langboard_shared.domain.models import AppDefinition, User
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261010002000-c62943df05b8.py"
    spec = importlib.util.spec_from_file_location("registry_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert {c["name"] for c in inspect(connection).get_columns("app_definition")} == set(AppDefinition.__table__.columns.keys()) - {"package"}
        package_spec = importlib.util.spec_from_file_location("package_migration", path.parent / "20261011210000-a75437bc41d2.py")
        package_module = importlib.util.module_from_spec(package_spec)
        package_spec.loader.exec_module(package_module)
        package_module.op = Operations(MigrationContext.configure(connection))
        package_module.upgrade()
        assert {c["name"] for c in inspect(connection).get_columns("app_definition")} == set(AppDefinition.__table__.columns.keys())
        with Session(connection) as db:
            from langboard_shared.core.types import SafeDateTime
            db.add(AppDefinition(id=100, key="example-erp", declaration=DECLARATION, approved_by=1,
                                 created_at=SafeDateTime.now(), updated_at=SafeDateTime.now()))
            db.flush()
        with pytest.raises(RuntimeError, match="Cannot discard"):
            module.downgrade()
        assert connection.execute(text("SELECT count(*) FROM app_definition")).scalar() == 1
        connection.execute(text("DELETE FROM app_definition"))
        module.downgrade()
        assert "app_definition" not in inspect(connection).get_table_names()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http"], indirect=True)
async def test_panel_consent_scope_revisions_and_revocation(board, monkeypatch, app_notifications):
    from langboard_shared.domain.models import BoardAppBinding

    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    from langboard_shared.domain.models import AppGovernancePolicy
    AppGovernancePolicy.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as session:
        session.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)

        async def request(method, path, **kwargs):
            return session.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)

        transport = HttpTransport(SimpleNamespace(request=request))
        registry = AppRegistry(transport)
        manager = AppManager(transport, board[2].get_uid())
        declaration = {**DECLARATION, "capabilities": ["panels.render"], "workflow_requirements": None}
        approved = await registry.approve(declaration)
        with pytest.raises(NativeApiError) as no_consent:
            await manager.panel("example-erp")
        assert no_consent.value.status_code == 404
        saved = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True))["binding"]
        assert saved["granted_capabilities"] == ["panels.render"]
        snapshot = await manager.panel("example-erp")
        assert snapshot["panel"] == declaration["panel"]
        assert snapshot["app_revision"] == approved["revision"]
        from langboard_shared.domain.services.AppGovernance import get_policy, save_policy
        policy = get_policy(actor)
        policy = save_policy(actor, "disabled", policy["revision"])
        retained = next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")
        assert retained["binding"]["revision"] == saved["revision"]
        assert retained["is_available"] is False and retained["capabilities"] == []
        with pytest.raises(NativeApiError) as forbidden_panel:
            await manager.panel("example-erp")
        assert forbidden_panel.value.status_code in (403, 404)
        cleared = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=False,
            binding_uid=saved["uid"], expected_revision=saved["revision"]))["binding"]
        assert cleared["granted_capabilities"] == []
        with pytest.raises(NativeApiError):
            await manager.set_panel_consent("example-erp", approved["revision"], enabled=True,
                binding_uid=cleared["uid"], expected_revision=cleared["revision"])
        save_policy(actor, "approved_only", policy["revision"])
        saved = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True,
            binding_uid=cleared["uid"], expected_revision=cleared["revision"]))["binding"]
        assert (await manager.panel("example-erp"))["panel"] == declaration["panel"]
        with DbSession.use(readonly=False) as db:
            binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == "example-erp")).first()
            assert binding.workflow_mapping == {} and not binding.stage_transitions_enabled
            actor.is_admin = False
            db.update(actor)
        # A read-only board member can read the consented panel, but cannot grant it.
        assert (await manager.panel("example-erp"))["key"] == "example-erp"
        with pytest.raises(NativeApiError) as denied:
            await manager.set_panel_consent("example-erp", approved["revision"], enabled=False,
                                            binding_uid=saved["uid"], expected_revision=saved["revision"])
        assert denied.value.status_code in (403, 404)
        with DbSession.use(readonly=False) as db:
            board[4].actions = ["read", "update"]
            db.update(board[4])
        notification_count = len(app_notifications)
        with pytest.raises(NativeApiError) as stale:
            await manager.set_panel_consent("example-erp", "0" * 64, enabled=False,
                                            binding_uid=saved["uid"], expected_revision=saved["revision"])
        assert stale.value.status_code == 409
        assert len(app_notifications) == notification_count
        disabled = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=False,
                                                   binding_uid=saved["uid"], expected_revision=saved["revision"]))["binding"]
        assert disabled["granted_capabilities"] == []
        with pytest.raises(NativeApiError):
            await manager.panel("example-erp")
        restored = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True,
                                                   binding_uid=disabled["uid"], expected_revision=disabled["revision"]))["binding"]
        assert restored["granted_capabilities"] == ["panels.render"]
        with DbSession.use(readonly=False) as db:
            actor.is_admin = True
            db.update(actor)
        newer = await registry.approve({**declaration, "version": "1.1.0"}, expected_revision=approved["revision"])
        assert (await manager.panel("example-erp"))["app_revision"] == newer["revision"]
        entry = next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")
        assert entry["app_revision"] == newer["revision"]
        restored = (await manager.set_panel_consent("example-erp", newer["revision"], enabled=True,
            binding_uid=entry["binding"]["uid"], expected_revision=entry["binding"]["revision"]))["binding"]
        disabled_app = await registry.disable("example-erp", newer["revision"])
        with pytest.raises(NativeApiError):
            await manager.panel("example-erp")
        retained = next(a for a in (await manager.catalog())["apps"] if a["key"] == "example-erp")
        binding_uid, revision = retained["binding"]["uid"], retained["binding"]["revision"]
        cleared = (await manager.set_panel_consent("example-erp", disabled_app["revision"], enabled=False,
            binding_uid=binding_uid, expected_revision=revision))["binding"]
        assert cleared["granted_capabilities"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
async def test_external_panel_read_needs_independent_consent_and_current_revision(board, monkeypatch):
    from langboard.routes.board import AppPanelDataApi
    native_read = AppPanelDataApi.list_board_signals
    requested_providers = []
    def tracked_read(*args, **kwargs):
        requested_providers.append(kwargs["provider"])
        return native_read(*args, **kwargs)
    monkeypatch.setattr(AppPanelDataApi, "list_board_signals", tracked_read)
    from langboard_shared.core.db import BaseDbModel
    BaseDbModel.metadata.create_all(DbEngine.get_main_engine())
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    from langboard_shared.domain.models import AppGovernancePolicy
    AppGovernancePolicy.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as session:
        session.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        async def request(method, path, **kwargs):
            return session.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)
        transport = HttpTransport(SimpleNamespace(request=request))
        registry = AppRegistry(transport)
        manager = AppManager(transport, board[2].get_uid())
        declaration = {**DECLARATION, "capabilities": ["panels.render", "signals.read"], "workflow_requirements": None, "signal_sources": ["github", "example-adapter"]}
        approved = await registry.approve({k: v for k, v in declaration.items() if k != "signal_sources"})
        saved = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True))["binding"]
        with pytest.raises(NativeApiError) as missing_sources:
            await manager.set_panel_consent("example-erp", approved["revision"], enabled=True, read_signals=True,
                binding_uid=saved["uid"], expected_revision=saved["revision"])
        assert missing_sources.value.status_code == 400
        denied_generic = await request("PUT", f"/board/{board[2].get_uid()}/settings/apps/example-erp/consent", json={
            "app_revision": approved["revision"], "binding_uid": saved["uid"],
            "expected_revision": saved["revision"], "capabilities": ["panels.render", "signals.read"],
        })
        assert denied_generic.status_code == 400

        declaration["version"] = "1.0.1"
        approved = await registry.approve(declaration, expected_revision=approved["revision"])
        catalog = await manager.catalog()
        external = next(item for item in catalog["apps"] if item["key"] == "example-erp")
        assert external["signal_sources"] == ["github", "example-adapter"]
        path = f"/board/{board[2].get_uid()}/apps/example-erp/panel/signals"
        form = {"app_revision": approved["revision"], "binding_revision": saved["revision"], "provider": "github"}
        denied = await request("POST", path, json=form)
        assert denied.status_code == 404
        assert requested_providers == []
        saved = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True, read_signals=True,
            binding_uid=saved["uid"], expected_revision=saved["revision"]))["binding"]
        assert saved["granted_capabilities"] == ["panels.render", "signals.read"]
        form["binding_revision"] = saved["revision"]
        result = await request("POST", path, json=form)
        assert result.status_code == 200 and result.json() == {"items": [], "next_cursor": None}
        for provider in ("glitchtip", "dokploy", "unsupported"):
            assert (await request("POST", path, json={**form, "provider": provider})).status_code == 404
        assert requested_providers == ["github"]
        # An explicitly approved future adapter still requires installed native support.
        assert (await request("POST", path, json={**form, "provider": "example-adapter"})).status_code == 400
        assert requested_providers == ["github", "example-adapter"]
        assert (await request("POST", path, json={**form, "provider": "../github"})).status_code == 400
        assert requested_providers == ["github", "example-adapter"]
        assert (await request("POST", path, json={**form, "binding_revision": "0" * 64})).status_code == 409
        approved = await registry.approve({**declaration, "version": "1.0.2", "signal_sources": ["github", "glitchtip"]},
            expected_revision=approved["revision"])
        current = next(item for item in (await manager.catalog())["apps"] if item["key"] == "example-erp")
        saved = current["binding"]
        assert saved["granted_capabilities"] == ["panels.render"]
        form.update(app_revision=approved["revision"], binding_revision=saved["revision"])
        assert (await request("POST", path, json={**form, "provider": "glitchtip"})).status_code == 404
        saved = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True, read_signals=True,
            binding_uid=saved["uid"], expected_revision=saved["revision"]))["binding"]
        form["binding_revision"] = saved["revision"]
        assert (await request("POST", path, json={**form, "provider": "glitchtip"})).status_code == 200
        assert (await request("POST", path, json={**form, "provider": "dokploy"})).status_code == 404

        disabled = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=False,
            binding_uid=saved["uid"], expected_revision=saved["revision"]))["binding"]
        assert disabled["granted_capabilities"] == ["signals.read"]
        assert (await request("POST", path, json=form)).status_code == 404

        # Closing a panel keeps the independent signal grant. Reopening may
        # explicitly withdraw that grant through the existing read checkbox.
        reopened = (await manager.set_panel_consent("example-erp", approved["revision"], enabled=True,
            binding_uid=disabled["uid"], expected_revision=disabled["revision"], read_signals=False))["binding"]
        assert reopened["granted_capabilities"] == ["panels.render"]
        form["binding_revision"] = reopened["revision"]
        assert (await request("POST", path, json=form)).status_code == 404

@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http"], indirect=True)
async def test_update_keeps_only_same_trust_grants_and_revokes_changed_connections(board, monkeypatch):
    from langboard_shared.domain.models import AppConnection, BoardAppBinding

    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    from langboard_shared.domain.models import AppGovernancePolicy
    AppGovernancePolicy.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    actor, project = board[1], board[2]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as session:
        session.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)

        async def request(method, path, **kwargs):
            return session.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)

        registry = AppRegistry(HttpTransport(SimpleNamespace(request=request)))
        previous = {**DECLARATION, "publisher": "Example Publisher", "capabilities": ["cards.create", "panels.render"]}
        saved = await registry.approve(previous)
        with DbSession.use(readonly=False) as db:
            binding = BoardAppBinding(project_id=project.id, app_key="example-erp", state="enabled",
                                      granted_capabilities=["cards.create", "panels.render"])
            connection = AppConnection(app_key="example-erp", owner_id=actor.id, state="connected")
            db.insert(binding)
            db.insert(connection)
            binding_id, connection_id = binding.id, connection.id
        ordinary = {**previous, "version": "1.1.0", "capabilities": ["panels.render", "signals.read"]}
        saved = await registry.approve(ordinary, expected_revision=saved["revision"])
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding_id)).first()
            assert current.state == "enabled" and current.granted_capabilities == ["panels.render"]
            assert db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection_id)).first().state == "connected"
        changed = {**ordinary, "version": "1.2.0", "publisher": "New Publisher"}
        with pytest.raises(NativeApiError) as stale:
            await registry.approve(changed, expected_revision="0" * 64)
        assert stale.value.status_code == 409
        await registry.approve(changed, expected_revision=saved["revision"])
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding_id)).first()
            assert current.state == "needs_attention" and current.granted_capabilities == []
            assert not current.stage_transitions_enabled
            assert db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection_id)).first().state == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
async def test_panel_signal_read_retains_native_resource_selection_and_type_scope(scoped, monkeypatch):
    from langboard_shared.core.db import BaseDbModel
    from langboard_shared.domain.models import CardAppSignalBinding
    from test_github_signal import send

    state = scoped[0]
    BaseDbModel.metadata.create_all(DbEngine.get_main_engine())
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    actor, project = state[1][1], state[1][2]
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
        link = db.exec(SqlBuilder.select.table(CardAppSignalBinding)).first()
        link.is_enabled = False
        db.update(link)
    signal = send(state)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as session:
        session.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        async def request(method, path, **kwargs):
            return session.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)
        transport = HttpTransport(SimpleNamespace(request=request))
        registry, manager = AppRegistry(transport), AppManager(transport, project.get_uid())
        declaration = {**DECLARATION, "key": "scoped-reader", "capabilities": ["panels.render", "signals.read"],
                       "resource_types": ["repository"], "workflow_requirements": None,
                       "signal_sources": ["github"]}
        declaration.pop("card_lifecycle")
        approved = await registry.approve(declaration)
        consent = (await manager.set_panel_consent("scoped-reader", approved["revision"], enabled=True, read_signals=True))["binding"]
        path = f"/board/{project.get_uid()}/apps/scoped-reader/panel/signals"
        form = {"app_revision": approved["revision"], "binding_revision": consent["revision"], "provider": "github"}
        response = await request("POST", path, json=form)
        assert response.status_code == 200, response.text
        assert [item["signal_uid"] for item in response.json()["items"]] == [signal["signal_uid"]]
        with DbSession.atomic() as db:
            state[4].is_selected = False
            db.update(state[4])
        response = await request("POST", path, json=form)
        assert response.status_code == 200 and response.json()["items"] == []
        with DbSession.atomic() as db:
            state[4].is_selected = True
            db.update(state[4])
        approved = await registry.approve({**declaration, "version": "1.0.1", "resource_types": ["issue"]},
                                          expected_revision=approved["revision"])
        current = next(item for item in (await manager.catalog())["apps"] if item["key"] == "scoped-reader")
        consent = (await manager.set_panel_consent("scoped-reader", approved["revision"], enabled=True, read_signals=True,
            binding_uid=current["binding"]["uid"], expected_revision=current["binding"]["revision"]))["binding"]
        form.update(app_revision=approved["revision"], binding_revision=consent["revision"])
        response = await request("POST", path, json=form)
        assert response.status_code == 200 and response.json()["items"] == []


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_registry_cursor_and_declaration_update_retain_installed_package(board, monkeypatch):
    from langboard_shared.domain.models import AppDefinition
    from langboard_shared.domain.services.AppRegistry import save_definition

    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    actor = board[1]
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
        for key in ("example-a", "example-b", "example-c"):
            db.insert(AppDefinition(key=key, approved_by=actor.id,
                                   declaration={**DECLARATION, "key": key}, package={"digest": key}))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        path = "/settings/apps/registry"
        first = client.get(path, headers=headers, params={"limit": 2})
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        assert [row["declaration"]["key"] for row in first.json()["apps"]] == ["example-a", "example-b"]
        assert first.json()["next_cursor"] == "example-b"
        second = client.get(path, headers=headers, params={"limit": 2, "after": "example-b"})
        assert [row["declaration"]["key"] for row in second.json()["apps"]] == ["example-c"]
        assert second.json()["next_cursor"] is None
        assert len(client.get(path, headers=headers).json()["apps"]) == 3
        assert client.get(path, headers=headers, params={"after": "example-b"}).status_code == 400
        assert client.get(path, headers=headers, params={"limit": 51}).status_code == 400
        revision = first.json()["apps"][0]["revision"]
        save_definition(actor, {**DECLARATION, "key": "example-a", "version": "1.0.1"}, revision)
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == "example-a")).first()
            assert current.package == {"digest": "example-a"}
            actor.is_admin = False
            db.update(actor)
        assert client.get(path, headers=headers, params={"limit": 2}).status_code == 403
