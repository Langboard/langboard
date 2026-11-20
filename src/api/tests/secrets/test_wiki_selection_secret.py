"""Stored selection conversion uses an isolated native DB and an in-memory Vault."""

import json
from types import SimpleNamespace
import pytest
from langboard.wiki_workspace.domain import WikiSnapshot
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import KeyVault
from langboard_shared.domain.models import (
    Bot,
    ProjectWiki,
    ProjectWikiActivity,
    ProjectWikiAssignedUser,
    SecretReference,
    SecretReferenceAudit,
    UserNotification,
)
from langboard_shared.domain.services.factory.ProjectWikiService import (
    ProjectWikiService,
    WikiSecretSelection,
    WikiSecretSelectionError,
    WikiSecretSelectionForm,
    wiki_secret_selection_bounds,
)
from langboard_shared.infrastructure.repositories.factory.ProjectWikiRepository import ProjectWikiRepository
from langboard_shared.tasks.activities.ProjectWikiActivityTask import project_wiki_updated as _native_wiki_updated


@pytest.fixture
def conversion(secrets, monkeypatch):
    from langboard.middlewares.CollaborativeEditMiddleware import CollaborativeEditMiddleware

    monkeypatch.setattr(CollaborativeEditMiddleware, "_get_active_collaborative_document_names", lambda *a: [])
    secret_service, board, _ = secrets
    engine = DbEngine.get_main_engine()
    ProjectWiki.__table__.create(engine)
    ProjectWikiAssignedUser.__table__.create(engine)
    Bot.__table__.create(engine, checkfirst=True)
    ProjectWikiActivity.__table__.create(engine)
    UserNotification.__table__.create(engine)
    material = {}

    def store(key, value):
        material[key] = value
        return key

    provider = SimpleNamespace(
        name=lambda: "fake-vault", store_secret=store, delete_key=lambda key: material.pop(key, None)
    )
    monkeypatch.setattr(KeyVault, "provider", provider)
    repo = ProjectWikiRepository(lambda _: None, lambda _: None)
    wiki_service = ProjectWikiService(lambda _: None, lambda _: None, SimpleNamespace(project_wiki=repo))
    monkeypatch.setattr(
        secret_service, "_raw_get_service", lambda cls: wiki_service if cls is ProjectWikiService else board[0]
    )
    events = []
    from langboard_shared.tasks.activities import ProjectWikiActivityTask

    monkeypatch.setattr(
        ProjectWikiActivityTask, "record_wiki_activity", lambda *args: events.append(("activity", args))
    )
    import importlib

    module = importlib.import_module(ProjectWikiService.__module__)
    monkeypatch.setattr(module.ProjectWikiPublisher, "updated", lambda *args: events.append(("socket", args)))
    monkeypatch.setattr(ProjectWikiService, "_publish_linked_card_changed", lambda *args: events.append(("card", args)))
    monkeypatch.setattr(
        module.ProjectWikiActivityTask, "project_wiki_updated", lambda *args: events.append(("activity", args))
    )
    monkeypatch.setattr(module.ProjectWikiBotTask, "project_wiki_updated", lambda *args: events.append(("bot", args)))
    source = "앞😀: fixture-sensitive-value suffix"
    with DbSession.use(readonly=False) as db:
        board[4].actions = ["read", "update"]
        db.update(board[4])
        wiki = ProjectWiki(project_id=board[2].id, title="Fixture", order=0, content=EditorContentModel(content=source))
        db.insert(wiki)
    start = len("앞😀: ".encode("utf-16-le")) // 2
    selection = WikiSecretSelection(start_utf16=start, end_utf16=start + len("fixture-sensitive-value"))
    return secret_service, board, wiki_service, wiki, source, selection, material, events


def call(fixture, **changes):
    service, board, _, wiki, source, selection, _, _ = fixture
    args = dict(
        actor=board[1],
        project_uid=board[2].get_uid(),
        wiki_uid=wiki.get_uid(),
        expected_revision=WikiSnapshot(wiki.get_uid(), wiki.title, source).revision,
        name="wiki/selection",
        selection=selection,
    )
    args.update(changes)
    return service.convert_wiki_selection(**args, collaboration_check=lambda generation: None)


def state(fixture):
    with DbSession.use(readonly=False) as db:
        wiki = db.exec(SqlBuilder.select.table(ProjectWiki)).first()
        refs = db.exec(SqlBuilder.select.table(SecretReference)).all()
        audits = db.exec(SqlBuilder.select.table(SecretReferenceAudit)).all()
        return wiki.content.content, refs, audits


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_success_masks_selected_content_and_only_dispatches_safe_after_commit(conversion):
    result = call(conversion)
    stored, refs, audits = state(conversion)
    assert stored == "앞😀: [••••](" + result["reference"]["uri"] + ") suffix"
    material = conversion[6]
    assert material[refs[0].locator] == "fixture-sensitive-value"
    assert set(material) == {refs[0].locator, refs[0].protection_locator}
    assert "fixture-sensitive-value" not in material[refs[0].protection_locator]
    assert len(refs) == len(audits) == 1
    assert audits[0].source_kind == "wiki"
    assert result["revision"] == WikiSnapshot("w", "t", stored).revision
    assert [event[0] for event in conversion[7]] == ["activity", "socket", "card", "bot"]
    activity = conversion[7][0][1][1]
    assert activity.activity_history["event"] == "secretized"
    assert "changes" not in activity.activity_history
    ordinary = json.dumps([result, [audit.model_dump(mode="json") for audit in audits]], default=str)
    ordinary += repr(conversion[7])
    assert "fixture-sensitive-value" not in ordinary and refs[0].locator not in ordinary


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "failure",
    ["stale", "membership", "read", "update", "private", "inactive", "bounds", "surrogate", "empty", "foreign"],
)
def test_invalid_or_revoked_selection_never_stores_material(conversion, failure):
    service, board, wiki_service, wiki, source, selection, material, events = conversion
    changes = {}
    with DbSession.use(readonly=False) as db:
        if failure == "membership":
            db.delete(board[3], purge=True)
        elif failure in {"read", "update"}:
            board[4].actions = ["update"] if failure == "read" else ["read"]
            db.update(board[4])
        elif failure == "inactive":
            board[1].activated_at = None
            db.update(board[1])
        elif failure == "private":
            wiki.is_public = False
            db.update(wiki)
    if failure == "stale":
        changes["expected_revision"] = "0" * 64
    if failure == "bounds":
        changes["selection"] = WikiSecretSelection(start_utf16=0, end_utf16=10000)
    if failure == "surrogate":
        changes["selection"] = WikiSecretSelection(start_utf16=2, end_utf16=3)
    if failure == "empty":
        changes["selection"] = WikiSecretSelection(start_utf16=1, end_utf16=1)
    if failure == "foreign":
        changes["project_uid"] = "B"
    with pytest.raises(WikiSecretSelectionError):
        call(conversion, **changes)
    assert not material and not events
    stored, refs, audits = state(conversion)
    assert stored == source and not refs and not audits


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("failure", ["vault", "audit", "wiki", "cleanup"])
def test_compensated_failures_leave_no_partial_db_or_event(conversion, monkeypatch, caplog, failure):
    service, _, wiki_service, _, source, _, material, events = conversion
    if failure == "vault":

        def failing_store(*args):
            raise RuntimeError("fixture-sensitive-value vault-locator")

        monkeypatch.setattr(KeyVault.provider, "store_secret", failing_store)
    elif failure == "audit":

        def failing_audit(*args):
            raise RuntimeError("fixture-sensitive-value vault-locator")

        monkeypatch.setattr(service, "_audit", failing_audit)
    else:

        def failing_update(*args):
            raise RuntimeError("fixture-sensitive-value vault-locator")

        monkeypatch.setattr(wiki_service.repo.project_wiki, "update_content_if_current", failing_update)
        if failure == "cleanup":

            def failing_delete(*args):
                raise RuntimeError("fixture-sensitive-value vault-locator")

            monkeypatch.setattr(KeyVault.provider, "delete_key", failing_delete)
    with pytest.raises(WikiSecretSelectionError) as caught:
        call(conversion)
    assert str(caught.value) == (
        "cleanup_required"
        if failure == "cleanup"
        else "storage_outcome_unknown"
        if failure == "vault"
        else "conversion_failed"
    )
    assert "fixture-sensitive-value" not in str(caught.value)
    assert "fixture-sensitive-value" not in caplog.text and "vault-locator" not in caplog.text
    stored, refs, audits = state(conversion)
    assert stored == source and not refs and not audits and not events
    assert bool(material) == (failure == "cleanup")


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_nested_transaction_is_rejected_without_storage(conversion):
    with DbSession.atomic():
        with pytest.raises(RuntimeError, match="must own"):
            call(conversion)
    assert not conversion[6]


@pytest.mark.parametrize("content,start,end", [("a😀b", 1, 3), ("abc", 0, 3)])
def test_utf16_bounds(content, start, end):
    left, right = wiki_secret_selection_bounds(content, WikiSecretSelection(start_utf16=start, end_utf16=end))
    assert content[left:right] == ("😀" if "😀" in content else content)


@pytest.mark.parametrize("content", ["a\nb", "**formatted**", "[••••](secret://ref/abc)", "# title"])
def test_unsupported_ranges_reject_before_storage(content):
    with pytest.raises(WikiSecretSelectionError, match="unsupported_selection"):
        wiki_secret_selection_bounds(content, WikiSecretSelection(start_utf16=0, end_utf16=len(content)))


def test_form_never_accepts_plaintext_or_slate_paths():
    from pydantic import ValidationError

    for extra in ({"text": "material"}, {"anchor": {"path": [0, 0], "offset": 1}}):
        with pytest.raises(ValidationError):
            WikiSecretSelectionForm.model_validate(
                dict(
                    expected_revision="a" * 64, name="wiki/name", selection={"start_utf16": 0, "end_utf16": 1, **extra}
                )
            )


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_commit_failure_redacts_actual_logger_and_compensates(conversion, caplog):
    from sqlalchemy import event

    engine = DbEngine.get_main_engine()

    def fail_commit(connection):
        raise RuntimeError("fixture-sensitive-value vault-locator-canary")

    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(WikiSecretSelectionError, match="conversion_failed"):
            call(conversion)
    finally:
        event.remove(engine, "commit", fail_commit)
    assert "Sensitive database transaction failed" in caplog.text
    assert "fixture-sensitive-value" not in caplog.text and "vault-locator-canary" not in caplog.text
    stored, refs, audits = state(conversion)
    assert stored == conversion[4] and not refs and not audits
    assert not conversion[6] and not conversion[7]


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_after_commit_failure_redacts_actual_logger_without_undoing_success(conversion, monkeypatch, caplog):
    import importlib

    module = importlib.import_module(ProjectWikiService.__module__)

    def fail_publish(*args):
        raise RuntimeError("fixture-sensitive-value vault-locator-canary")

    monkeypatch.setattr(module.ProjectWikiPublisher, "updated", fail_publish)
    result = call(conversion)
    assert result["reference"]["state"] == "active"
    assert "Sensitive after-commit callback failed" in caplog.text
    assert "fixture-sensitive-value" not in caplog.text and "vault-locator-canary" not in caplog.text
    stored, refs, audits = state(conversion)
    assert stored != conversion[4] and len(refs) == len(audits) == 1
    assert set(conversion[6]) == {refs[0].locator, refs[0].protection_locator}


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_native_http_metadata_contract_and_current_authority(conversion, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board.BoardWikiApi import convert_wiki_selection_to_secret
    from langboard_shared.core.caching import Cache
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    secret_service, board, _, wiki, source, selection, material, _ = conversion
    actor = board[1]
    wrapper = SimpleNamespace(secret_reference=secret_service, project_wiki=conversion[2], close=lambda: None)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(Cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(Cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: wrapper
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is convert_wiki_selection_to_secret:
            for dep in route.dependant.dependencies:
                if dep.name == "service":
                    app.dependency_overrides[dep.call] = lambda: wrapper
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    path = f"/board/{board[2].get_uid()}/wiki/{wiki.get_uid()}/secret-selection"
    form = {
        "expected_revision": WikiSnapshot("w", "t", source).revision,
        "name": "wiki/http",
        "selection": selection.model_dump(),
    }
    with TestClient(app) as client:
        assert client.post(path, json=form).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        assert client.post(path, json={**form, "text": "plaintext"}, headers=headers).status_code == 400
        assert client.post(path, json={**form, "expected_revision": "0" * 64}, headers=headers).status_code == 409
        assert not material
        response = client.post(path, json=form, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert "fixture-sensitive-value" not in response.text and "locator" not in response.text
        assert response.json()["reference"]["scope"] == "project"
        assert client.post(path, json=form, headers=headers).status_code == 409


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_real_duplicate_constraint_rolls_back_new_vault_value(conversion, caplog):
    call(conversion)
    stored, refs, _ = state(conversion)
    first_material = dict(conversion[6])
    conversion[7].clear()
    with DbSession.use(readonly=False) as db:
        second = ProjectWiki(
            project_id=conversion[1][2].id,
            title="Second",
            order=1,
            content=EditorContentModel(content="new-sensitive-value"),
        )
        db.insert(second)
    with pytest.raises(WikiSecretSelectionError, match="conversion_failed"):
        call(
            conversion,
            wiki_uid=second.get_uid(),
            expected_revision=WikiSnapshot("w", "t", "new-sensitive-value").revision,
            selection=WikiSecretSelection(start_utf16=0, end_utf16=19),
        )
    assert state(conversion)[0] == stored
    assert len(state(conversion)[1]) == 1
    assert conversion[6] == first_material and not conversion[7]
    assert "fixture-sensitive-value" not in caplog.text and refs[0].locator not in caplog.text


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_uncertain_provider_write_is_reported_without_false_atomicity(conversion, monkeypatch):
    material = conversion[6]

    def uncertain_store(key, value):
        material[key] = value
        raise RuntimeError("fixture-sensitive-value ambiguous-locator")

    monkeypatch.setattr(KeyVault.provider, "store_secret", uncertain_store)
    with pytest.raises(WikiSecretSelectionError, match="storage_outcome_unknown"):
        call(conversion)
    stored, refs, audits = state(conversion)
    assert stored == conversion[4] and not refs and not audits and not conversion[7]
    # The provider never returned its locator. Physical cleanup cannot be asserted.
    assert len(material) == 1


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize(
    "source,start,end", [("~~credential~~", 2, 12), ("| credential |", 2, 12), ("x\rcredential", 2, 12)]
)
def test_unsupported_literal_lines_rejected_before_vault_storage(conversion, monkeypatch, source, start, end):
    service, board, _, wiki, _, _, material, events = conversion
    with DbSession.use(readonly=False) as db:
        wiki.content = EditorContentModel(content=source)
        db.update(wiki)
    calls = []
    monkeypatch.setattr(KeyVault.provider, "store_secret", lambda *args: calls.append(args))
    with pytest.raises(WikiSecretSelectionError, match="unsupported_selection"):
        call(
            conversion,
            expected_revision=WikiSnapshot("w", "t", source).revision,
            selection=WikiSecretSelection(start_utf16=start, end_utf16=end),
        )
    assert not calls and not material and not events
    assert state(conversion)[0] == source and not state(conversion)[1]


@pytest.fixture
def collaborative_reproduction_client(conversion, monkeypatch):
    """Exercise JWT authentication and the real collaborative middleware boundary."""
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.CollaborativeEditMiddleware import CollaborativeEditMiddleware
    from langboard.routes.board.BoardWikiApi import convert_wiki_selection_to_secret
    from langboard_shared.core.caching import Cache
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    secret_service, board, _, wiki, source, selection, _, _ = conversion
    wrapper = SimpleNamespace(secret_reference=secret_service, project_wiki=conversion[2], close=lambda: None)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(Cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(Cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: wrapper
    )
    app = FastAPI()
    assert any(getattr(route, "endpoint", None) is convert_wiki_selection_to_secret for route in AppRouter.api.routes)
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(CollaborativeEditMiddleware)
    access, refresh = AuthSecurity.authenticate(board[1].id)
    path = f"/board/{board[2].get_uid()}/wiki/{wiki.get_uid()}/secret-selection"
    form = {
        "expected_revision": WikiSnapshot("w", "t", source).revision,
        "name": "wiki/collaboration-reproduction",
        "selection": selection.model_dump(),
    }
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        yield client, path, form, {"Authorization": f"Bearer {access}"}


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("draft_state", ["active", "unknown"])
def test_collaborative_jwt_conversion_must_not_write_when_active_or_unknown(
    conversion, collaborative_reproduction_client, monkeypatch, draft_state, record_property
):
    from unittest.mock import Mock
    from langboard.middlewares.CollaborativeEditMiddleware import CollaborativeEditMiddleware

    document_name = f"wiki:{conversion[3].get_uid()}:content"
    coordinator = Mock(return_value=[document_name] if draft_state == "active" else None)
    monkeypatch.setattr(CollaborativeEditMiddleware, "_get_active_collaborative_document_names", coordinator)
    client, path, form, headers = collaborative_reproduction_client
    response = client.post(path, json=form, headers=headers)
    facts = {
        "entrypoint": "native-jwt-http",
        "draft_state": draft_state,
        "http_status": response.status_code,
        "coordinator_calls": coordinator.call_count,
        "vault_writes": len(conversion[6]),
        "wiki_changed": state(conversion)[0] != conversion[4],
    }
    record_property("missing_fence_reproduction", facts)
    print(facts)
    assert not conversion[6] and state(conversion)[0] == conversion[4]
    assert response.status_code in {409, 503}


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("draft_state", ["active", "unknown"])
def test_collaborative_native_mcp_conversion_must_not_write_when_active_or_unknown(
    conversion, monkeypatch, draft_state, record_property
):
    from unittest.mock import Mock
    from langboard.mcp_tools.WikiMcp import convert_wiki_selection_to_secret
    from langboard.middlewares.CollaborativeEditMiddleware import CollaborativeEditMiddleware

    service, board, _, wiki, source, selection, _, _ = conversion
    document_name = f"wiki:{wiki.get_uid()}:content"
    coordinator = Mock(return_value=[document_name] if draft_state == "active" else None)
    monkeypatch.setattr(CollaborativeEditMiddleware, "_get_active_collaborative_document_names", coordinator)
    from fastmcp.exceptions import ValidationError

    with pytest.raises(ValidationError, match="collaboration_active|collaboration_unavailable"):
        convert_wiki_selection_to_secret(
            board[2].get_uid(),
            wiki.get_uid(),
            WikiSnapshot("w", "t", source).revision,
            "wiki/mcp-collaboration-reproduction",
            selection,
            board[1],
            SimpleNamespace(secret_reference=service),
        )
    result = {}
    facts = {
        "entrypoint": "native-mcp-command",
        "draft_state": draft_state,
        "coordinator_calls": coordinator.call_count,
        "vault_writes": len(conversion[6]),
        "wiki_changed": state(conversion)[0] != source,
        "reference_created": bool(result.get("reference")),
    }
    record_property("missing_fence_reproduction", facts)
    print(facts)
    assert not conversion[6] and state(conversion)[0] == source


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_generation_rejects_delayed_save_and_missing_writer(conversion):
    from langboard_shared.core.exceptions.WikiContentConflict import WikiContentConflict

    _, board, wiki_service, old_wiki, source, _, _, _ = conversion
    result = call(conversion)
    assert result["editor_generation"] == 1
    stored = state(conversion)[0]
    for generation in (None, 0):
        with pytest.raises(WikiContentConflict):
            wiki_service.update(
                board[1],
                board[2],
                old_wiki,
                {"content": EditorContentModel(content=source)},
                expected_generation=generation,
                expected_content=source,
            )
    assert state(conversion)[0] == stored
    # The repository enforces generation even when the attacker knows current source.
    old_wiki.content = EditorContentModel(content=source)
    assert not wiki_service.repo.project_wiki.update_content_if_current(old_wiki, stored, expected_generation=0)
    assert state(conversion)[0] == stored


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_old_generation_cannot_save_even_when_join_races_conversion(conversion):
    _, board, wiki_service, old_wiki, source, _, _, _ = conversion
    admitted = []
    service = conversion[0]
    result = service.convert_wiki_selection(
        board[1],
        board[2].get_uid(),
        old_wiki.get_uid(),
        WikiSnapshot("w", "t", source).revision,
        "wiki/racing-join",
        conversion[5],
        collaboration_check=lambda generation: admitted.append(generation),
    )
    assert admitted == [0] and result["editor_generation"] == 1
    # An owner admitted after an idle preflight still has generation 0. Its eventual save loses.
    old_wiki.content = EditorContentModel(content=source)
    stored = state(conversion)[0]
    assert not wiki_service.repo.project_wiki.update_content_if_current(
        old_wiki, stored, expected_generation=admitted[0]
    )
    assert state(conversion)[0] == stored


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_current_generation_can_save_and_stale_source_cannot(conversion):
    call(conversion)
    stored = state(conversion)[0]
    wiki_service = conversion[2]
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectWiki)).first()
    current.content = EditorContentModel(content=stored + " edited")
    assert wiki_service.repo.project_wiki.update_content_if_current(current, stored, expected_generation=1)
    assert state(conversion)[0] == stored + " edited"
    current.content = EditorContentModel(content=conversion[4])
    assert not wiki_service.repo.project_wiki.update_content_if_current(current, stored, expected_generation=1)
    assert state(conversion)[0] == stored + " edited"


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_native_generation_authorization_rejects_old_and_unknown_lineages(conversion, monkeypatch):
    import importlib

    module = importlib.import_module("langboard.routes.auth.SocketAuthorization")
    monkeypatch.setattr(module, "is_subscription_authorized", lambda *a: True)
    wiki = conversion[3]
    wrapper = SimpleNamespace(project_wiki=SimpleNamespace(get_by_id_like=lambda uid: wiki))
    current = f"wiki:{wiki.get_uid()}:content:0"
    assert module.authorized_editor_document_subscription(wrapper, conversion[1][1], current)
    for name in (
        current[:-1] + "1",
        current[:-1] + "-1",
        current[:-1] + "00",
        current[:-1] + "bad",
        current[:-1] + "9" * 5000,
        current[:-1] + "2147483648",
    ):
        assert module.authorized_editor_document_subscription(wrapper, conversion[1][1], name) is None
    call(conversion)
    wiki.editor_generation = 1
    assert module.authorized_editor_document_subscription(wrapper, conversion[1][1], current) is None
    assert module.authorized_editor_document_subscription(wrapper, conversion[1][1], current[:-1] + "1")


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_native_http_delayed_content_save_cannot_restore_plaintext(
    conversion, collaborative_reproduction_client, monkeypatch
):
    service = conversion[2]
    monkeypatch.setattr(service, "is_assigned", lambda *a: True)
    client, path, _, headers = collaborative_reproduction_client
    call(conversion)
    masked = state(conversion)[0]
    path = path.replace("secret-selection", "details")
    base = {"content": {"content": conversion[4]}, "expected_revision": WikiSnapshot("w", "t", conversion[4]).revision}
    for extra in ({}, {"editor_generation": 0}, {"editor_generation": 1}):
        response = client.put(path, json={**base, **extra}, headers=headers)
        assert response.status_code == 409
    assert state(conversion)[0] == masked


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_native_mcp_delayed_replace_cannot_restore_plaintext(conversion, monkeypatch):
    from fastmcp.exceptions import ValidationError
    from langboard.mcp_tools.WikiMcp import replace_wiki_content

    service = conversion[2]
    # Snapshot reads may use the replica in production; final shared CAS is on primary.
    # This isolated fixture points both pools at its owned DB instead of the app-preview sentinel.
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(service, "convert_to_api_response", lambda *a: {"forbidden": False})
    monkeypatch.setattr(service, "is_assigned", lambda *a: True)
    call(conversion)
    masked = state(conversion)[0]
    wrapper = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda uid: conversion[1][2]), project_wiki=service
    )
    with pytest.raises(ValidationError):
        replace_wiki_content(
            conversion[1][2].get_uid(),
            conversion[3].get_uid(),
            WikiSnapshot("w", "t", conversion[4]).revision,
            conversion[4],
            conversion[1][1],
            wrapper,
        )
    assert state(conversion)[0] == masked


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_generation_migration_preserves_source_and_requires_safe_downgrade(board):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024040000-72f4c6e8a190.py"
    spec = importlib.util.spec_from_file_location("wiki_generation_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = DbEngine.get_main_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE project_wiki (id INTEGER PRIMARY KEY, content TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO project_wiki VALUES (1, 'preserved historical source')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            row = connection.execute(text("SELECT content, editor_generation FROM project_wiki")).one()
            assert row == ("preserved historical source", 0)
            connection.execute(text("UPDATE project_wiki SET editor_generation=1"))
            with pytest.raises(RuntimeError, match="cannot be dropped"):
                module.downgrade()
            connection.execute(text("UPDATE project_wiki SET editor_generation=0"))
            module.downgrade()
            assert (
                connection.execute(text("SELECT content FROM project_wiki")).scalar() == "preserved historical source"
            )


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_postgres_conversion_commit_fences_writer_waiting_on_same_row(conversion):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep
    from sqlalchemy import text

    entered, release, writer_started = Event(), Event(), Event()
    service, board, wiki_service, old_wiki, source, selection, _, _ = conversion
    engine = DbEngine.get_main_engine()

    def owner_check(generation):
        assert generation == 0
        entered.set()
        assert release.wait(5)

    def convert():
        return service.convert_wiki_selection(
            board[1],
            board[2].get_uid(),
            old_wiki.get_uid(),
            WikiSnapshot("w", "t", source).revision,
            "wiki/concurrent",
            selection,
            collaboration_check=owner_check,
        )

    def delayed_save():
        old_wiki.content = EditorContentModel(content=source + " stale edit")
        writer_started.set()
        return wiki_service.repo.project_wiki.update_content_if_current(old_wiki, source, expected_generation=0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        conversion_future = pool.submit(convert)
        try:
            assert entered.wait(5)
            stale_future = pool.submit(delayed_save)
            assert writer_started.wait(5)
            deadline = monotonic() + 2
            waiting = False
            while monotonic() < deadline:
                with engine.connect() as connection:
                    waiting = bool(
                        connection.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock' AND query LIKE '%project_wiki%'"
                            )
                        ).scalar()
                    )
                if waiting:
                    break
                sleep(0.01)
            assert waiting, "stale writer must actually wait on conversion's live wiki row lock"
        finally:
            release.set()
        assert conversion_future.result(timeout=5)["editor_generation"] == 1
        assert stale_future.result(timeout=5) is False
    stored, refs, audits = state(conversion)
    assert "fixture-sensitive-value" not in stored and len(refs) == len(audits) == 1


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_board_chat_uses_current_generation_without_relabeling_old_context(conversion):
    from langboard.routes.auth.forms.Socket import SocketBoardChatStartForm
    from langboard.routes.auth.SocketAuthApi import _board_chat_active_documents

    wiki = conversion[3]
    name = f"wiki:{wiki.get_uid()}:content:0"
    run = SimpleNamespace(scope_table="project_wiki", scope_uid=wiki.get_uid())
    form = SocketBoardChatStartForm(active_document_names=[name])
    assert _board_chat_active_documents(form, run, conversion[1][2].get_uid())[0]["section"] == "content"
    call(conversion)
    for old in (name, name.rsplit(":", 1)[0]):
        with pytest.raises(Exception) as caught:
            _board_chat_active_documents(
                SocketBoardChatStartForm(active_document_names=[old]), run, conversion[1][2].get_uid()
            )
        assert getattr(caught.value, "status_code", None) == 403
    fresh = name[:-1] + "1"
    assert (
        _board_chat_active_documents(
            SocketBoardChatStartForm(active_document_names=[fresh]), run, conversion[1][2].get_uid()
        )[0]["document_name"]
        == fresh
    )


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_native_current_generation_http_mcp_and_title_only_remain_usable(
    conversion, collaborative_reproduction_client, monkeypatch
):
    from langboard.mcp_tools.WikiMcp import append_wiki_content

    service = conversion[2]
    monkeypatch.setattr(service, "is_assigned", lambda *a: True)
    monkeypatch.setattr(service, "convert_to_api_response", lambda *a: {"forbidden": False})
    client, path, _, headers = collaborative_reproduction_client
    call(conversion)
    monkeypatch.setattr(
        service, "_raw_get_service", lambda cls: SimpleNamespace(notify_mentioned_in_wiki=lambda *a: None)
    )
    masked = state(conversion)[0]
    details = path.replace("secret-selection", "details")
    changed = masked + " reviewed edit"
    response = client.put(
        details,
        json={
            "content": {"content": changed},
            "editor_generation": 1,
            "expected_revision": WikiSnapshot("w", "t", masked).revision,
        },
        headers=headers,
    )
    assert response.status_code == 200 and response.json()["editor_generation"] == 1
    assert state(conversion)[0] == changed
    response = client.put(details, json={"title": "Still editable"}, headers=headers)
    assert response.status_code == 200
    wrapper = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda uid: conversion[1][2]), project_wiki=service
    )
    result = append_wiki_content(
        conversion[1][2].get_uid(),
        conversion[3].get_uid(),
        WikiSnapshot("w", "t", changed).revision,
        "native append",
        conversion[1][1],
        wrapper,
    )
    assert result["wiki_uid"] == conversion[3].get_uid()
    with DbSession.use(readonly=False) as db:
        wiki = db.exec(SqlBuilder.select.table(ProjectWiki)).first()
        assert wiki.editor_generation == 2 and "native append" in wiki.content.content
    assert "fixture-sensitive-value" not in state(conversion)[0]


@pytest.fixture
def admission_conversion(conversion, monkeypatch, tmp_path):
    """Use a real file and separate SQLite connections for native lock ordering."""
    import sqlite3
    from sqlalchemy import create_engine

    engine = DbEngine.get_main_engine()
    if engine.dialect.name != "sqlite":
        yield conversion
        return
    path = tmp_path / "admission.sqlite"
    with engine.connect() as source:
        with sqlite3.connect(path) as destination:
            source.connection.driver_connection.backup(destination)
    owned = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.15})
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: owned)
    try:
        yield conversion
    finally:
        owned.dispose()


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_final_admission_rejects_conversion_in_progress_and_committed(admission_conversion):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from langboard.routes.auth.SocketAuthorization import editor_document_generation_is_current

    conversion = admission_conversion
    entered, release = Event(), Event()
    service, board, _, wiki, source, selection, _, _ = conversion
    name = f"wiki:{wiki.get_uid()}:content:0"

    def check(generation):
        entered.set()
        assert release.wait(5)

    def convert():
        return service.convert_wiki_selection(
            board[1],
            board[2].get_uid(),
            wiki.get_uid(),
            WikiSnapshot("w", "t", source).revision,
            "wiki/concurrent-admission",
            selection,
            collaboration_check=check,
        )

    # Preliminary authority is valid before native registration.
    assert editor_document_generation_is_current(name)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(convert)
        try:
            assert entered.wait(5)
            # MVCC still returns the old generation; this unlocked check is unsafe.
            assert editor_document_generation_is_current(name)
            # Registration happens now. NOWAIT must reject instead of deadlocking
            # on the conversion's callback or admitting its old visible snapshot.
            assert not editor_document_generation_is_current(name, admission=True)
        finally:
            release.set()
        assert future.result(timeout=5)["editor_generation"] == 1
    assert not editor_document_generation_is_current(name, admission=True)
    assert editor_document_generation_is_current(name[:-1] + "1", admission=True)


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_admission_lock_orders_conversion_owner_preflight(admission_conversion, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep
    from langboard.routes.auth.SocketAuthorization import editor_document_generation_is_current
    from sqlalchemy import text

    conversion = admission_conversion
    locked, release, attempted = Event(), Event(), Event()
    service, board, _, wiki, source, selection, _, _ = conversion
    name = f"wiki:{wiki.get_uid()}:content:0"
    original_exec = DbSession.exec
    engine = DbEngine.get_main_engine()
    owner_registered = True

    def controlled_exec(self, statement, *args, **kwargs):
        result = original_exec(self, statement, *args, **kwargs)
        clause = getattr(statement, "_for_update_arg", None)
        if (clause is not None and clause.nowait) or str(statement) == "BEGIN IMMEDIATE" and not locked.is_set():
            locked.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(DbSession, "exec", controlled_exec)

    def preflight(generation):
        assert owner_registered
        # Callback uses the unlocked path even while conversion holds the row.
        assert editor_document_generation_is_current(name)
        raise WikiSecretSelectionError("collaboration_active")

    def convert():
        with engine.connect() as db:
            db.execute(text("select 1"))
        attempted.set()
        return service.convert_wiki_selection(
            board[1],
            board[2].get_uid(),
            wiki.get_uid(),
            WikiSnapshot("w", "t", source).revision,
            "wiki/ordered-admission",
            selection,
            collaboration_check=preflight,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        admission = pool.submit(editor_document_generation_is_current, name, admission=True)
        try:
            assert locked.wait(5)
            conversion_future = pool.submit(convert)
            assert attempted.wait(5)
            deadline = monotonic() + 2
            waiting = False
            while monotonic() < deadline:
                if engine.dialect.name == "sqlite":
                    from sqlalchemy.exc import OperationalError

                    with engine.connect() as probe:
                        probe.execute(text("PRAGMA busy_timeout=0"))
                        with pytest.raises(OperationalError, match="locked"):
                            probe.execute(text("BEGIN IMMEDIATE"))
                    waiting = True
                    break
                with engine.connect() as connection:
                    waiting = bool(
                        connection.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock' "
                                "AND query LIKE '%project_wiki%'"
                            )
                        ).scalar()
                    )
                if waiting:
                    break
                sleep(0.02)
            assert waiting, "final admission must hold the native database reservation"
            assert not conversion_future.done()
        finally:
            release.set()
        assert admission.result(timeout=5)
        with pytest.raises(WikiSecretSelectionError, match="collaboration_active"):
            conversion_future.result(timeout=5)
    assert state(conversion)[0] == source
    assert not conversion[6]


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("is_public", [True, False])
def test_delayed_publicity_cannot_publish_preconversion_content(conversion, monkeypatch, is_public):
    """Resume a captured publicity request after native conversion commits."""
    import importlib
    from langboard_shared.infrastructure.repositories.factory.ProjectWikiAssignedUserRepository import (
        ProjectWikiAssignedUserRepository,
    )

    module = importlib.import_module(ProjectWikiService.__module__)
    _, board, service, stale_wiki, source, _, _, _ = conversion
    service.repo.project_wiki_assigned_user = ProjectWikiAssignedUserRepository(lambda _: None, lambda _: None)
    service.repo.project_assigned_user = SimpleNamespace(find_by_user_and_project=lambda *args: board[3])
    for tasks in (module.ProjectWikiActivityTask, module.ProjectWikiBotTask):
        monkeypatch.setattr(tasks, "project_wiki_publicity_changed", lambda *args: None)
    publications = []
    monkeypatch.setattr(
        module.ProjectWikiPublisher, "put_dispather", lambda model, event: publications.append((model, event))
    )
    original_update = service.repo.project_wiki.update

    def conversion_before_publicity_update(wiki):
        result = call(conversion)
        assert result["editor_generation"] == 1
        assert wiki.content.content == source and wiki.editor_generation == 0
        return original_update(wiki)

    monkeypatch.setattr(service.repo.project_wiki, "update", conversion_before_publicity_update)
    result = service.change_public(board[1], board[2], stale_wiki, is_public)
    assert result is not None
    stored, _, _ = state(conversion)
    assert "fixture-sensitive-value" not in stored
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectWiki)).first()
        assert current.editor_generation == 1 and current.is_public == is_public
        assignments = db.exec(SqlBuilder.select.table(ProjectWikiAssignedUser)).all()
        assert len(assignments) == (0 if is_public else 1)
        if assignments:
            assert assignments[0].user_id == board[1].id
    assert len(publications) == 1
    payload, event = publications[0]
    assert "fixture-sensitive-value" not in json.dumps(payload, default=str)
    assert set(payload["wiki"]) == {"uid", "is_public", "assigned_members"}
    assert payload["wiki"]["uid"] == stale_wiki.get_uid()
    assert payload["wiki"]["is_public"] == is_public
    assert len(payload["wiki"]["assigned_members"]) == (0 if is_public else 1)
    assert event.event == f"board:wiki:public:changed:{stale_wiki.get_uid()}"
    # Legacy fromOne updates only supplied keys: safe on an existing model.
    legacy_model = {"content": stored, "editor_generation": 1, **payload["wiki"]}
    legacy_model.update(payload["wiki"])
    assert legacy_model["content"] == stored and legacy_model["editor_generation"] == 1


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("rollback", [False, True])
def test_secretization_bounds_history_reads_and_keeps_all_batches_atomic(conversion, monkeypatch, rollback):
    from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    from sqlalchemy import event

    _, board, _, wiki, source, _, _, _ = conversion
    with DbSession.use(readonly=False) as db:
        for index in range(203):
            db.insert(
                ProjectWikiActivity(
                    user_id=board[1].id,
                    project_id=board[2].id,
                    project_wiki_id=wiki.id,
                    activity_type=ProjectWikiActivityType.WikiUpdated,
                    activity_history={"changes": {"before": {"content": source}}, "retained": index},
                )
            )
    reads = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "project_wiki_activity.activity_history" in statement:
            reads.append(statement)

    def fail_after_history(*args):
        raise RuntimeError("fixture failure after history masking")

    if rollback:
        monkeypatch.setattr(NotificationService, "redact_wiki_mentions", fail_after_history)
    engine = DbEngine.get_main_engine()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        if rollback:
            with pytest.raises(WikiSecretSelectionError):
                call(conversion)
        else:
            call(conversion)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(ProjectWikiActivity)).all()
    originals = [row for row in rows if "retained" in row.activity_history]
    assert len(originals) == 203
    assert {row.activity_history["retained"] for row in originals} == set(range(203))
    assert all(("fixture-sensitive-value" in json.dumps(row.activity_history)) is rollback for row in originals)
    assert len(rows) == (203 if rollback else 204)
    assert len(reads) >= 3 and all("LIMIT" in statement for statement in reads)


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_secretization_redacts_only_proven_history_content_and_preserves_metadata(conversion):
    from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType

    _, board, _, wiki, source, _, _, _ = conversion
    with DbSession.use(readonly=False) as db:
        activity = ProjectWikiActivity(
            user_id=board[1].id,
            project_id=board[2].id,
            project_wiki_id=wiki.id,
            activity_type=ProjectWikiActivityType.WikiUpdated,
            activity_history={
                "wiki": {"title": "Original title"},
                "unrelated": {"value": "keep"},
                "changes": {
                    "before": {"title": "Old title", "content": {"content": source}},
                    "after": {"title": "New title", "content": {"content": source + "\nnormal"}},
                },
            },
        )
        clean = ProjectWikiActivity(
            user_id=board[1].id,
            project_id=board[2].id,
            project_wiki_id=wiki.id,
            activity_type=ProjectWikiActivityType.WikiUpdated,
            activity_history={
                "changes": {
                    "before": {"content": {"content": "ordinary before"}},
                    "after": {"content": {"content": "ordinary after"}},
                }
            },
        )
        db.insert(activity)
        db.insert(clean)
    with DbSession.use(readonly=False) as db:
        original = db.exec(
            SqlBuilder.select.table(ProjectWikiActivity).where(ProjectWikiActivity.id == activity.id)
        ).first()
        original_timestamps = original.created_at, original.updated_at
    call(conversion)
    with DbSession.use(readonly=False) as db:
        redacted = db.exec(
            SqlBuilder.select.table(ProjectWikiActivity).where(ProjectWikiActivity.id == activity.id)
        ).first()
        unchanged = db.exec(
            SqlBuilder.select.table(ProjectWikiActivity).where(ProjectWikiActivity.id == clean.id)
        ).first()
    assert "fixture-sensitive-value" not in json.dumps(redacted.api_response(), default=str)
    assert redacted.activity_history["changes"]["after"]["content"]["content"] == "앞😀: [••••] suffix\nnormal"
    assert redacted.activity_history["unrelated"] == {"value": "keep"}
    assert redacted.activity_history["changes"]["before"]["title"] == "Old title"
    assert redacted.user_id == activity.user_id
    assert (redacted.created_at, redacted.updated_at) == original_timestamps
    assert unchanged.activity_history == clean.activity_history


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("secretize", [True, False])
def test_delayed_activity_uses_secretization_fence_without_dropping_ordinary_history(
    conversion, monkeypatch, secretize
):
    import asyncio
    from langboard_shared.core.db import EditorContentModel
    from langboard_shared.domain.models import Bot

    Bot.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    _, board, _, wiki, source, _, _, _ = conversion
    snapshot = wiki.model_copy(deep=True)
    if secretize:
        call(conversion)
    else:
        # An ordinary generation advance must not erase pending history.
        with DbSession.use(readonly=False) as db:
            wiki.editor_generation += 1
            db.update(wiki)
    asyncio.run(
        _native_wiki_updated.__wrapped__(
            board[1],
            board[2],
            {"content": EditorContentModel(content="ordinary before"), "title": "Before"},
            snapshot,
        )
    )
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(ProjectWikiActivity).order_by(ProjectWikiActivity.id)).all()
    activity = rows[-1]
    changes = activity.activity_history["changes"]
    assert changes["before"]["title"] == "Before"
    if secretize:
        assert "content" not in changes["before"] and "content" not in changes["after"]
        assert activity.activity_history["secret_redacted"] is True
        assert "fixture-sensitive-value" not in json.dumps(activity.api_response(), default=str)
    else:
        assert changes["after"]["content"]["content"] == source
        assert "secret_redacted" not in activity.activity_history


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_secretization_history_and_fence_roll_back_together(conversion, monkeypatch):
    from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType
    from langboard_shared.tasks.activities import ProjectWikiActivityTask

    _, board, _, wiki, source, _, material, events = conversion
    history = {"changes": {"after": {"content": {"content": source}}}}
    with DbSession.use(readonly=False) as db:
        db.insert(
            ProjectWikiActivity(
                user_id=board[1].id,
                project_id=board[2].id,
                project_wiki_id=wiki.id,
                activity_type=ProjectWikiActivityType.WikiUpdated,
                activity_history=history,
            )
        )

    def fail(*args):
        raise RuntimeError("Activity persistence unavailable")

    monkeypatch.setattr(ProjectWikiActivityTask, "record_wiki_secretized", fail)
    with pytest.raises(WikiSecretSelectionError, match="conversion_failed"):
        call(conversion)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(ProjectWikiActivity)).all()
    assert len(rows) == 1 and rows[0].activity_history == history
    stored, refs, audits = state(conversion)
    assert stored == source and not refs and not audits and not material and not events


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_activity_cannot_insert_old_snapshot_across_conversion_reservation(admission_conversion):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.exc import OperationalError

    conversion = admission_conversion
    service, board, _, wiki, source, selection, _, _ = conversion
    snapshot = wiki.model_copy(deep=True)

    def delayed():
        return asyncio.run(
            _native_wiki_updated.__wrapped__(
                board[1],
                board[2],
                {"content": EditorContentModel(content="ordinary before")},
                snapshot,
            )
        )

    with ThreadPoolExecutor(max_workers=1) as pool:

        def check(generation):
            # Conversion owns SQLite's writer; the stale activity must fail closed.
            pending = pool.submit(delayed)
            with pytest.raises(OperationalError, match="locked"):
                pending.result(timeout=5)

        service.convert_wiki_selection(
            board[1],
            board[2].get_uid(),
            wiki.get_uid(),
            WikiSnapshot("w", "t", source).revision,
            "wiki/reserved-history",
            selection,
            collaboration_check=check,
        )
        pool.submit(delayed).result(timeout=5)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(ProjectWikiActivity)).all()
    assert len(rows) == 2
    assert all("fixture-sensitive-value" not in json.dumps(row.api_response(), default=str) for row in rows)


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_post_secretization_ordinary_edits_keep_content_diff(conversion):
    import asyncio
    from langboard_shared.domain.models import Bot

    Bot.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    call(conversion)
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectWiki)).first()
        before = current.content
        current.content = EditorContentModel(content=current.content.content + " ordinary addition")
        db.update(current)
    asyncio.run(_native_wiki_updated.__wrapped__(conversion[1][1], conversion[1][2], {"content": before}, current))
    with DbSession.use(readonly=False) as db:
        activity = db.exec(SqlBuilder.select.table(ProjectWikiActivity).order_by(ProjectWikiActivity.id.desc())).first()
    changes = activity.activity_history["changes"]
    assert changes["before"]["content"]["content"] == before.content
    assert changes["after"]["content"]["content"].endswith("ordinary addition")
    assert "secret_redacted" not in activity.activity_history


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("rollback", [False, True])
def test_secret_conversion_bounds_mention_reads_and_rolls_back_every_batch(conversion, monkeypatch, rollback):
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.tasks.activities import ProjectWikiActivityTask
    from sqlalchemy import event

    _, board, _, wiki, source, _, _, _ = conversion
    with DbSession.use(readonly=False) as db:
        for index in range(203):
            for wiki_id in (wiki.id, wiki.id + 1):
                db.insert(
                    UserNotification(
                        notifier_type="user",
                        notifier_id=board[1].id,
                        receiver_id=board[1].id,
                        notification_type=NotificationType.MentionedInWiki,
                        message_vars={"line": source, "retained": index},
                        record_list=[("project", board[2].id), ("project_wiki", wiki_id)],
                    )
                )
    reads = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "user_notification.message_vars" in statement:
            reads.append(statement)

    def fail_after_notifications(*args):
        raise RuntimeError("fixture failure after notification masking")

    if rollback:
        monkeypatch.setattr(ProjectWikiActivityTask, "record_wiki_secretized", fail_after_notifications)
    engine = DbEngine.get_main_engine()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        if rollback:
            with pytest.raises(WikiSecretSelectionError):
                call(conversion)
        else:
            call(conversion)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserNotification)).all()
    assert len(rows) == 406
    for row in rows:
        target = any(table == "project_wiki" and record_id == wiki.id for table, record_id in row.record_list)
        assert row.message_vars["retained"] in range(203)
        assert ("fixture-sensitive-value" in row.message_vars["line"]) is (rollback or not target)
    assert len(reads) >= 3 and all("LIMIT" in statement for statement in reads)


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_secret_conversion_masks_stored_wiki_mention_line(conversion):
    from langboard_shared.core.utils.EditorContentParser import find_mentioned
    from langboard_shared.domain.models import UserNotification
    from langboard_shared.domain.models.UserNotification import NotificationType

    engine = DbEngine.get_main_engine()
    UserNotification.__table__.create(engine, checkfirst=True)
    _, board, _, wiki, source, _, _, _ = conversion
    # The native mention parser copies surrounding exact wiki text into durable message_vars.
    _, lines = find_mentioned(EditorContentModel(content=source + " [**@reader**](target)"))
    line = lines["target"]
    rows = []
    with DbSession.use(readonly=False) as db:
        for wiki_id in (wiki.id, wiki.id + 1):
            row = UserNotification(
                notifier_type="user",
                notifier_id=board[1].id,
                receiver_id=board[1].id,
                notification_type=NotificationType.MentionedInWiki,
                message_vars={"line": line, "retained": "metadata"},
                record_list=[("project", board[2].id), ("project_wiki", wiki_id)],
            )
            db.insert(row)
            rows.append(row)
    with DbSession.use(readonly=False) as db:
        original = db.exec(SqlBuilder.select.table(UserNotification).where(UserNotification.id == rows[0].id)).first()
        timestamps = (original.created_at, original.updated_at)
    call(conversion)
    with DbSession.use(readonly=False) as db:
        masked = db.exec(SqlBuilder.select.table(UserNotification).where(UserNotification.id == rows[0].id)).first()
        untouched = db.exec(SqlBuilder.select.table(UserNotification).where(UserNotification.id == rows[1].id)).first()
        assert "fixture-sensitive-value" not in masked.message_vars["line"]
        assert masked.message_vars["line"] == line.replace("fixture-sensitive-value", "[••••]")
        assert masked.message_vars["retained"] == "metadata"
        assert (masked.created_at, masked.updated_at) == timestamps
        assert untouched.message_vars["line"] == line


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_wiki_mentions_reject_stale_generation_and_use_current_source(conversion, monkeypatch):
    from langboard_shared.domain.services.factory.NotificationService import NotificationService

    _, board, _, old_wiki, _, _, _, _ = conversion
    service = NotificationService(lambda _: None, lambda _: None, None)
    observed = []
    monkeypatch.setattr(
        service, "_NotificationService__notify_mentioned", lambda *args: observed.append(args[1].content)
    )
    service.notify_mentioned_in_wiki(board[1], board[2], old_wiki)
    assert observed == [old_wiki.content.content]
    observed.clear()
    call(conversion)
    service.notify_mentioned_in_wiki(board[1], board[2], old_wiki)
    assert not observed
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == old_wiki.id)).first()
    # Equal-generation detached arguments still cannot supply stale body text.
    current.content = old_wiki.content
    service.notify_mentioned_in_wiki(board[1], board[2], current)
    assert not observed
    with DbSession.use(readonly=False) as db:
        fresh = db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == old_wiki.id)).first()
    service.notify_mentioned_in_wiki(board[1], board[2], fresh)
    assert len(observed) == 1 and "fixture-sensitive-value" not in observed[0]


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_deferred_wiki_mention_fanout_reloads_redacted_durable_line(conversion, monkeypatch):
    from langboard_shared.Env import Env

    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    from langboard_shared.publishers.UserPublisher import UserPublisher

    _, board, _, wiki, source, _, _, _ = conversion
    with DbSession.use(readonly=False) as db:
        notification = UserNotification(
            notifier_type="user",
            notifier_id=board[1].id,
            receiver_id=board[1].id,
            notification_type=NotificationType.MentionedInWiki,
            message_vars={"line": source},
            record_list=[("project", board[2].id), ("project_wiki", wiki.id)],
        )
        db.insert(notification)
    stale_payload = notification.api_response()
    from langboard_shared.domain.models import UserNotificationUnsubscription
    from langboard_shared.domain.services import DomainService

    UserNotificationUnsubscription.__table__.create(DbEngine.get_main_engine())
    call(conversion)
    completed, published = [], []
    service = NotificationService(
        DomainService()._create_or_get_product,
        lambda _: None,
        SimpleNamespace(
            user_notification=SimpleNamespace(complete_web_fanout=lambda row: completed.append(row.id)),
        ),
    )
    monkeypatch.setattr(UserPublisher, "notified", lambda recipient, payload: published.append(payload))
    assert service._publish_web_notification(notification, board[1], stale_payload)
    assert completed == [notification.id] and len(published) == 1
    assert published[0]["message_vars"]["line"] == source.replace("fixture-sensitive-value", "[••••]")
    assert notification.message_vars["line"] == source  # The pending closure really held a stale copy.


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "state",
    [
        "allowed",
        "membership_revoked",
        "private_wiki",
        "deleted_user",
        "inactive_user",
        "deleted_wiki",
        "unsubscribed",
        "transport_failure",
        "resolver_failure",
    ],
)
@pytest.mark.parametrize("delivery", ["delayed", "recovered"])
def test_delayed_wiki_notification_rechecks_native_recipient(conversion, monkeypatch, state, delivery):
    from langboard_shared.Env import Env

    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import UserNotificationUnsubscription
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.models.UserNotificationUnsubscription import NotificationChannel, NotificationScope
    from langboard_shared.domain.services import DomainService
    from langboard_shared.publishers.UserPublisher import UserPublisher

    _, board, _, wiki, source, _, _, _ = conversion
    UserNotificationUnsubscription.__table__.create(DbEngine.get_main_engine())
    service = DomainService().notification
    with DbSession.atomic() as db:
        notification = UserNotification(
            notifier_type="user",
            notifier_id=board[1].id,
            receiver_id=board[1].id,
            notification_type=NotificationType.MentionedInWiki,
            message_vars={"line": source},
            record_list=[("project", board[2].id), ("project_wiki", wiki.id)],
            web_visible=True,
            web_fanout_pending=True,
        )
        db.insert(notification)
    stale_payload = notification.api_response()
    with DbSession.atomic() as db:
        if state == "membership_revoked":
            db.delete(board[3])
        elif state == "private_wiki":
            wiki.is_public = False
            db.update(wiki)
        elif state == "deleted_user":
            board[1].deleted_at = SafeDateTime.now()
            db.update(board[1])
        elif state == "inactive_user":
            board[1].activated_at = None
            db.update(board[1])
        elif state == "deleted_wiki":
            wiki.deleted_at = SafeDateTime.now()
            db.update(wiki)
        elif state == "unsubscribed":
            db.insert(
                UserNotificationUnsubscription(
                    user_id=board[1].id,
                    channel=NotificationChannel.Web,
                    notification_type=NotificationType.MentionedInWiki,
                    scope_type=NotificationScope.Specific,
                    specific_table="project_wiki",
                    specific_id=wiki.id,
                )
            )
    published = []

    def publish(*args):
        if state == "transport_failure":
            raise ConnectionError("fixture transport unavailable")
        published.append(args)

    monkeypatch.setattr(UserPublisher, "notified", publish)
    if state == "resolver_failure":

        def unavailable(*args):
            raise ConnectionError("fixture authority unavailable")

        monkeypatch.setattr(service, "_resolve_notification_recipient", unavailable)
    retry = state in {"transport_failure", "resolver_failure"}
    if delivery == "recovered":
        from datetime import timedelta

        with DbSession.atomic() as db:
            db.exec(
                SqlBuilder.update.table(UserNotification)
                .where(UserNotification.id == notification.id)
                .values(
                    updated_at=SafeDateTime.now() - timedelta(minutes=1),
                )
            )
        # Keep envelope reconstruction on this fixture's primary native database.
        with DbSession.atomic():
            assert service.recover_pending_web_fanout() == (0 if retry else 1)
    else:
        assert service._publish_web_notification(notification, board[1], stale_payload) is not retry
    assert len(published) == (1 if state == "allowed" else 0)
    with DbSession.use(readonly=False) as db:
        current = db.exec(
            SqlBuilder.select.table(UserNotification).where(UserNotification.id == notification.id)
        ).first()
        assert current.web_fanout_pending is retry
        assert current.message_vars == {"line": source}
    if retry:
        if state == "resolver_failure":
            monkeypatch.setattr(
                service,
                "_resolve_notification_recipient",
                type(service)._resolve_notification_recipient.__get__(service),
            )
        monkeypatch.setattr(UserPublisher, "notified", lambda *args: published.append(args))
        assert service._publish_web_notification(notification, board[1], stale_payload)
        assert len(published) == 1
        with DbSession.use(readonly=False) as db:
            current = db.exec(
                SqlBuilder.select.table(UserNotification).where(UserNotification.id == notification.id)
            ).first()
            assert current.web_fanout_pending is False


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_inflight_wiki_mention_creation_serializes_with_secret_conversion(conversion, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.services.factory.NotificationService import NotificationService

    _, board, _, wiki, source, _, _, _ = conversion
    source += f"\n[**@reader**]({board[1].get_uid()})"
    wiki.content = EditorContentModel(content=source)
    with DbSession.use(readonly=False) as db:
        db.update(wiki)
    conversion = (*conversion[:4], source, *conversion[5:])
    entered, release, converted = Event(), Event(), Event()
    service = NotificationService(lambda _: None, lambda _: None, None)

    def persist_mention(*args):
        entered.set()
        assert release.wait(5)
        with DbSession.use(readonly=False) as db:
            db.insert(
                UserNotification(
                    notifier_type="user",
                    notifier_id=board[1].id,
                    receiver_id=board[1].id,
                    notification_type=NotificationType.MentionedInWiki,
                    message_vars={"line": args[1].content},
                    record_list=[("project", board[2].id), ("project_wiki", wiki.id)],
                )
            )

    monkeypatch.setattr(service, "_NotificationService__notify_mentioned", persist_mention)

    def convert():
        result = call(conversion)
        converted.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(service.notify_mentioned_in_wiki, board[1], board[2], wiki)
        assert entered.wait(5)
        secretized = pool.submit(convert)
        try:
            assert not converted.wait(0.15)
        finally:
            release.set()
        pending.result(timeout=5)
        assert secretized.result(timeout=5)["editor_generation"] == 1
    with DbSession.use(readonly=False) as db:
        notifications = db.exec(SqlBuilder.select.table(UserNotification)).all()
        assert len(notifications) == 1
        assert notifications[0].message_vars["line"] == source.replace("fixture-sensitive-value", "[••••]")


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_wiki_mention_redaction_rolls_back_with_failed_conversion(conversion, monkeypatch):
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.tasks.activities import ProjectWikiActivityTask

    _, board, _, wiki, source, _, _, _ = conversion
    with DbSession.use(readonly=False) as db:
        notification = UserNotification(
            notifier_type="user",
            notifier_id=board[1].id,
            receiver_id=board[1].id,
            notification_type=NotificationType.MentionedInWiki,
            message_vars={"line": source},
            record_list=[("project", board[2].id), ("project_wiki", wiki.id)],
        )
        db.insert(notification)

    def fail(*args):
        raise RuntimeError("transaction fence failure")

    monkeypatch.setattr(ProjectWikiActivityTask, "record_wiki_secretized", fail)
    with pytest.raises(WikiSecretSelectionError, match="conversion_failed"):
        call(conversion)
    with DbSession.use(readonly=False) as db:
        current = db.exec(
            SqlBuilder.select.table(UserNotification).where(UserNotification.id == notification.id)
        ).first()
        assert current.message_vars["line"] == source
    stored, refs, _ = state(conversion)
    assert stored == source and not refs and not conversion[6] and not conversion[7]


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_native_wiki_bot_producer_broker_payload_contains_only_identifiers(conversion, monkeypatch):
    from langboard_shared.core.broker.TaskParameters import TaskParameters

    _, board, service, wiki, source, _, _, events = conversion
    monkeypatch.setattr(
        service, "_raw_get_service", lambda _: SimpleNamespace(notify_mentioned_in_wiki=lambda *a: None)
    )
    assert service.update(
        board[1],
        board[2],
        wiki,
        {"content": EditorContentModel(content=source + " appended")},
        expected_content=source,
        expected_generation=0,
    )
    queued = [args for kind, args in events if kind == "bot"]
    assert len(queued) == 1
    packed = TaskParameters(*queued[0]).pack()
    assert "fixture-sensitive-value" not in json.dumps(packed, default=str)
    assert all(type(value) in (str, int) for value in queued[0])


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_wiki_bot_consumer_rehydrates_current_scope_and_rejects_old_generation(conversion, monkeypatch):
    import asyncio
    from langboard_shared.domain.constants.DomainEventType import DomainEventType
    from langboard_shared.tasks.bots.ProjectWikiBotTask import _run_wiki_event, wiki_event_args
    from langboard_shared.tasks.bots.utils import BotTaskHelper

    _, board, _, wiki, _, _, _, _ = conversion
    dispatched = []
    monkeypatch.setattr(BotTaskHelper, "get_scoped_bots", lambda *a, **kw: ["current-scoped-bot"])

    async def run(bots, event, data, project):
        dispatched.append((bots, event, data))

    monkeypatch.setattr(BotTaskHelper, "run", run)
    args = wiki_event_args(board[1], board[2], wiki)
    asyncio.run(_run_wiki_event(DomainEventType.ProjectWikiUpdated, *args))
    assert len(dispatched) == 1 and dispatched[0][0] == ["current-scoped-bot"]
    assert dispatched[0][2]["project_wiki_uid"] == wiki.get_uid()
    assert "fixture-sensitive-value" not in json.dumps(dispatched, default=str)
    dispatched.clear()
    call(conversion)
    asyncio.run(_run_wiki_event(DomainEventType.ProjectWikiUpdated, *args))
    assert not dispatched
    current_args = (*args[:-1], 1)
    asyncio.run(_run_wiki_event(DomainEventType.ProjectWikiUpdated, *current_args))
    assert len(dispatched) == 1
    dispatched.clear()
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == wiki.id)).first()
        current.is_public = False
        db.update(current)
    asyncio.run(_run_wiki_event(DomainEventType.ProjectWikiUpdated, *current_args))
    assert not dispatched


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_bot_mention_native_broker_never_contains_wiki_or_credentials(conversion, monkeypatch):
    from langboard_shared.core.broker.TaskParameters import TaskParameters
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    from langboard_shared.tasks.bots import BotDefaultTask

    _, board, _, wiki, source, _, _, _ = conversion
    queued = []
    with DbSession.use(readonly=False) as db:
        bot = Bot(
            name="Mentioned",
            bot_uname="mention-fixture",
            app_api_token="fixture-bot-credential",
            api_key="fixture-api-credential",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(bot)
        wiki.content = EditorContentModel(content=source + f"\n[**@Mentioned**]({bot.get_uid()})")
        db.update(wiki)
    service = NotificationService(lambda _: None, lambda _: None, None)
    monkeypatch.setattr(service, "_NotificationService__notify", lambda *a: False)
    monkeypatch.setattr(BotDefaultTask, "bot_mentioned", lambda *a: queued.append(a))
    service.notify_mentioned_in_wiki(board[1], board[2], wiki)
    assert len(queued) == 1
    packed = json.dumps(TaskParameters(*queued[0]).pack(), default=str)
    for marker in ("fixture-sensitive-value", "fixture-bot-credential", "fixture-api-credential", '"password"'):
        assert marker not in packed
    assert all(type(value) in (str, int, list, type(None)) for value in queued[0])


@pytest.fixture
def mention_scope(conversion):
    from langboard_shared.domain.models import BotDefaultScopeBranch, Card, CardComment, ProjectBotScope
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType

    engine = DbEngine.get_main_engine()
    for model in (BotDefaultScopeBranch, ProjectBotScope, Card, CardComment):
        model.__table__.create(engine, checkfirst=True)
    _, board, _, wiki, _, _, _, _ = conversion
    with DbSession.atomic() as db:
        bot = Bot(
            name="Mentioned",
            bot_uname="scope-fixture",
            app_api_token="fixture-bot-credential",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(bot)
        hook = ProjectBotScope(bot_id=bot.id, project_id=board[2].id, conditions=[], default_scope_branch_id=None)
        db.insert(hook)
        card = Card(
            project_id=board[2].id,
            project_column_id=board[5][0].id,
            title="Fixture card",
            description=EditorContentModel(content="fixture-card-body"),
            visibility="SHARED",
        )
        db.insert(card)
        comment = CardComment(
            card_id=card.id, user_id=board[1].id, content=EditorContentModel(content="fixture-comment-body")
        )
        db.insert(comment)
    return conversion, bot, hook, card, comment


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("mentioned_in", ["card", "comment", "project_wiki"])
def test_bot_mention_current_card_comment_wiki_dispatch(mention_scope, monkeypatch, mentioned_in):
    import asyncio
    from langboard_shared.tasks.bots.BotDefaultTask import bot_mentioned, mention_event_args
    from langboard_shared.tasks.bots.utils import BotTaskHelper

    conversion, bot, _, card, comment = mention_scope
    _, board, _, wiki, _, _, _, _ = conversion
    models = [board[2], wiki] if mentioned_in == "project_wiki" else [board[2], board[5][0], card]
    if mentioned_in == "comment":
        models.append(comment)
    dispatched = []

    async def run(target, event, data, project, scope):
        dispatched.append((target.get_uid(), data, scope.get_uid()))

    monkeypatch.setattr(BotTaskHelper, "run", run)
    args = mention_event_args(board[1], bot, mentioned_in, models)
    asyncio.run(bot_mentioned.__wrapped__(*args))
    assert len(dispatched) == 1 and dispatched[0][0] == bot.get_uid()
    assert dispatched[0][1]["mentioned_in"] == mentioned_in
    assert dispatched[0][1]["project_uid"] == board[2].get_uid()
    if mentioned_in == "comment":
        assert dispatched[0][1]["comment_uid"] == comment.get_uid()
    for marker in ("fixture-sensitive-value", "fixture-bot-credential", "fixture-card-body", "fixture-comment-body"):
        assert marker not in json.dumps(dispatched, default=str)


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "denied",
    [
        "hook_removed",
        "actor_removed",
        "private_wiki",
        "old_generation",
        "wrong_project",
        "private_card",
        "wrong_comment",
        "deleted_source",
    ],
)
def test_bot_mention_rechecks_current_scope_before_dispatch(mention_scope, monkeypatch, denied):
    import asyncio
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import CardComment
    from langboard_shared.tasks.bots.BotDefaultTask import bot_mentioned, mention_event_args
    from langboard_shared.tasks.bots.utils import BotTaskHelper

    conversion, bot, hook, card, comment = mention_scope
    _, board, _, wiki, _, _, _, _ = conversion
    mentioned_in = "project_wiki" if denied in {"private_wiki", "old_generation", "wrong_project"} else "card"
    if denied == "wrong_comment":
        mentioned_in = "comment"
    models = [board[2], wiki] if mentioned_in == "project_wiki" else [board[2], card]
    if mentioned_in == "comment":
        models.append(comment)
    args = mention_event_args(board[1], bot, mentioned_in, models)
    with DbSession.atomic() as db:
        if denied == "hook_removed":
            db.delete(hook)
        elif denied == "actor_removed":
            db.delete(board[3])
        elif denied == "private_wiki":
            wiki.is_public = False
            db.update(wiki)
        elif denied == "old_generation":
            wiki.editor_generation += 1
            db.update(wiki)
        elif denied == "wrong_project":
            wiki.project_id = 11
            db.update(wiki)
        elif denied == "private_card":
            from langboard_shared.domain.models import Card

            private = Card(
                project_id=board[2].id,
                project_column_id=board[5][0].id,
                title="Private",
                visibility="PRIVATE",
                owner_user_id=board[1].id,
                created_by_user_id=board[1].id,
            )
            db.insert(private)
            args = (
                *args[:4],
                [(table, private.get_uid() if table == "card" else uid) for table, uid in args[4]],
                args[5],
            )
        elif denied == "wrong_comment":
            wrong = CardComment(card_id=card.id + 1, user_id=board[1].id)
            # Use an existing foreign-project card so the native FK stays meaningful.
            from langboard_shared.domain.models import Card

            foreign = Card(project_id=11, project_column_id=board[5][0].id, title="Other")
            db.insert(foreign)
            wrong.card_id = foreign.id
            db.insert(wrong)
            args = (
                *args[:4],
                [(table, wrong.get_uid() if table == "card_comment" else uid) for table, uid in args[4]],
                args[5],
            )
        elif denied == "deleted_source":
            card.deleted_at = SafeDateTime.now()
            db.update(card)
    dispatched = []

    async def run(*args):
        dispatched.append(args)

    monkeypatch.setattr(BotTaskHelper, "run", run)
    asyncio.run(bot_mentioned.__wrapped__(*args))
    assert not dispatched


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_assigned_bot_manual_scope_run_queues_only_native_references(mention_scope, monkeypatch):
    from langboard_shared.core.broker.TaskParameters import TaskParameters
    from langboard_shared.domain.services.factory.OrchestrationTaskService import OrchestrationTaskService
    from langboard_shared.tasks.bots import BotDefaultTask

    conversion, bot, _, card, _ = mention_scope
    board = conversion[1]
    queued = []
    monkeypatch.setattr(BotDefaultTask, "bot_mentioned", lambda *args: queued.append(args))
    service = OrchestrationTaskService(lambda _: None, lambda _: None, None)
    with DbSession.atomic():
        service._OrchestrationTaskService__dispatch_assigned_bot(
            board[1],
            board[2],
            card,
            {"assigned_bot_uid": bot.get_uid()},
        )
    assert len(queued) == 1 and queued[0][3] == "card"
    packed = json.dumps(TaskParameters(*queued[0]).pack())
    assert "fixture-card-body" not in packed and "fixture-bot-credential" not in packed
    assert queued[0][4] == [("project", board[2].get_uid()), ("card", card.get_uid())]


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "name", ["fixture-sensitive-value", "wiki/fixture-sensitive-value", "fixture-sensitive-value-copy"]
)
def test_secret_selection_cannot_be_exposed_as_public_reference_name(conversion, name, monkeypatch):
    storage_attempts = []
    monkeypatch.setattr(KeyVault.provider, "store_secret", lambda *args: storage_attempts.append(True))
    with pytest.raises(WikiSecretSelectionError, match="invalid_name"):
        call(conversion, name=name)
    stored, refs, audits = state(conversion)
    assert stored == conversion[4] and not refs and not audits
    assert not storage_attempts and not conversion[6] and not conversion[7]


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_secret_selection_allows_unrelated_public_reference_name(conversion):
    result = call(conversion, name="wiki/database-password")
    stored, refs, audits = state(conversion)
    assert result["reference"]["name"] == "wiki/database-password"
    assert len(refs) == len(audits) == 1
    assert "fixture-sensitive-value" not in json.dumps(result)
    assert "fixture-sensitive-value" not in stored
    material = conversion[6]
    assert material[refs[0].locator] == "fixture-sensitive-value"
    assert set(material) == {refs[0].locator, refs[0].protection_locator}
    assert "fixture-sensitive-value" not in material[refs[0].protection_locator]


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("operation", ["rename", "copy", "rotate"])
def test_converted_secret_metadata_cannot_publish_material(conversion, monkeypatch, caplog, operation):
    from pydantic import SecretStr

    service, board, _, _, _, _, material, events = conversion
    monkeypatch.setattr(KeyVault.provider, "get_key", material.__getitem__, raising=False)
    reference = call(conversion)["reference"]
    before_material, before_events = dict(material), list(events)
    with pytest.raises(ValueError, match="Invalid logical secret name"):
        if operation == "rename":
            service.rename(board[1], reference["uri"], "wiki/fixture-sensitive-value-copy", 0)
        elif operation == "copy":
            service.copy(board[1], reference["uri"], "wiki/fixture-sensitive-value-copy", 0)
        else:
            service.rotate(board[1], reference["uri"], SecretStr("selection"), 0)
    stored, refs, audits = state(conversion)
    assert len(refs) == len(audits) == 1
    assert refs[0].metadata() == reference
    assert material == before_material and events == before_events
    assert service.get_metadata(board[1], reference["uri"]) == reference
    assert "fixture-sensitive-value" not in stored + caplog.text + json.dumps(reference)


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_converted_secret_legitimate_metadata_updates_and_authorized_resolution(conversion, monkeypatch):
    from langboard_shared.domain.services.factory.SecretReferenceService import SecretReferenceUnavailable
    from pydantic import SecretStr

    service, board, _, _, _, _, material, _ = conversion
    monkeypatch.setattr(KeyVault.provider, "get_key", material.__getitem__, raising=False)
    reference = call(conversion)["reference"]
    renamed = service.rename(board[1], reference["uri"], "wiki/database-password", 0)
    copied = service.copy(board[1], reference["uri"], "wiki/database-password-copy", 1)
    rotated = service.rotate(board[1], reference["uri"], SecretStr("replacement-sensitive-value"), 1)
    assert renamed["name"] == "wiki/database-password" and rotated["revision"] == 2
    assert service.resolve_for_runtime(board[1], copied["uri"]).get_secret_value() == "fixture-sensitive-value"
    assert service.resolve_for_runtime(board[1], rotated["uri"]).get_secret_value() == "replacement-sensitive-value"
    with DbSession.atomic() as db:
        board[4].actions = ["read"]
        db.update(board[4])
    with pytest.raises(SecretReferenceUnavailable):
        service.rename(board[1], rotated["uri"], "wiki/ordinary-name", 2)
    with pytest.raises(SecretReferenceUnavailable):
        service.resolve_for_runtime(board[1], rotated["uri"])


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_queued_wiki_mention_before_conversion_retains_only_invalidation(conversion, monkeypatch):
    from langboard_shared.core.broadcast import DispatcherQueue
    from langboard_shared.domain.models import UserNotificationUnsubscription
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.services import DomainService
    from langboard_shared.Env import Env

    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    _, board, _, wiki, source, _, _, _ = conversion
    UserNotificationUnsubscription.__table__.create(DbEngine.get_main_engine())
    service = DomainService().notification
    with DbSession.atomic() as db:
        notification = UserNotification(
            notifier_type="user",
            notifier_id=board[1].id,
            receiver_id=board[1].id,
            notification_type=NotificationType.MentionedInWiki,
            message_vars={"line": source},
            record_list=[("project", board[2].id), ("project_wiki", wiki.id)],
            web_visible=True,
            web_fanout_pending=True,
        )
        db.insert(notification)
    queued = []
    # Capture the actual serialized copy made by the real UserPublisher.
    monkeypatch.setattr(DispatcherQueue, "put", lambda message: queued.append(message.model_dump_json()))
    payload = service.convert_to_api_response(notification, [board[2], wiki], board[1])
    assert service._publish_web_notification(notification, board[1], payload)
    assert len(queued) == 1
    envelope_before_conversion = queued[0]
    result = call(conversion)
    with DbSession.use(readonly=False) as db:
        current = db.exec(
            SqlBuilder.select.table(UserNotification).where(UserNotification.id == notification.id)
        ).first()
        assert current.message_vars["line"] == source.replace("fixture-sensitive-value", "[••••]")
        assert [(table, int(record_id)) for table, record_id in current.record_list] == [
            (table, int(record_id)) for table, record_id in notification.record_list
        ]
        assert current.receiver_id == board[1].id and current.read_at is None
        assert current.web_fanout_pending is False
        fresh_wiki = db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == wiki.id)).first()
    refreshed = service.convert_to_api_response(current, [board[2], fresh_wiki], board[1])
    assert refreshed["uid"] == payload["uid"]
    assert refreshed["type"] == NotificationType.MentionedInWiki.value
    assert refreshed["records"] == payload["records"]
    assert refreshed["notifier_user"] == payload["notifier_user"]
    assert "fixture-sensitive-value" not in json.dumps(refreshed, default=str)
    assert result["reference"]["uri"] in fresh_wiki.content.content
    # SQL masking cannot retract a JSON envelope already copied into a queue.
    assert queued == [envelope_before_conversion]
    assert "fixture-sensitive-value" not in envelope_before_conversion
    event = json.loads(envelope_before_conversion)["data"]
    assert event["data"] == {"notification": {"uid": notification.get_uid(), "type": "mentioned_in_wiki"}}
    assert event["publish_models"]["event"] == "user:notified"
    assert event["publish_models"]["topic_id"] == board[1].get_uid()
    assert event["publish_models"]["data_keys"] == ["notification"]
