# ruff: noqa: F811
"""Native card/metadata/lineage writes; current app scope before every replay."""

from concurrent.futures import ThreadPoolExecutor
from json import loads
import pytest
from langboard_shared.core.db import BaseDbModel, DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    AppConnection,
    AppDefinition,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppOwnershipAudit,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
    CardMetadata,
    ExternalImportRecord,
    ProjectAssignedUser,
    WorkflowStageDefinition,
)
from langboard_shared.domain.services.AppCardCreation import AppCardCreationConflict, create_app_card
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.Env import Env
from langboard_shared.helpers import ensure_models_imported
from sqlalchemy import text


PRESENTATION = {
    "version": 1,
    "key": "app.example-erp.issue",
    "axis": "type",
    "name": "ERP issue",
    "description": "Issue reported by an external ERP application.",
    "icon": "📋",
}


@pytest.fixture
def creation(board, monkeypatch):
    _, actor, project, member, role, columns, stages = board
    engine = DbEngine.get_main_engine()
    if engine.dialect.name != "postgresql":
        pytest.skip("Native card creation requires PostgreSQL sequence/readiness SQL")
    from langboard_shared.domain.models import Organization
    from sqlalchemy import inspect
    from sqlalchemy.schema import CreateColumn

    # Some board fixtures provide the full organization; older ones use an ID stub.
    organization_columns = {column["name"] for column in inspect(engine).get_columns("organization")}
    with engine.begin() as db:
        for column in Organization.__table__.columns:
            if column.name not in organization_columns:
                db.execute(
                    text(
                        "ALTER TABLE organization ADD COLUMN "
                        + str(CreateColumn(column).compile(dialect=engine.dialect))
                    )
                )
    ensure_models_imported()
    BaseDbModel.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            db.execute(text("CREATE SEQUENCE content_change_seq"))
            # This migration-owned SQL table is outside ORM create_all.
            db.execute(text("CREATE TABLE card_execution_generation (card_id BIGINT PRIMARY KEY REFERENCES card(id) ON DELETE CASCADE, execution_generation INTEGER NOT NULL DEFAULT 0)"))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    events = []
    monkeypatch.setattr(CardService, "dispatch_created", lambda self, *args: events.append(args[3].get_uid()))
    with DbSession.atomic() as db:
        project.owner_id = actor.id
        db.update(project)
        columns[0].workflow_stage = "backlog"
        db.update(columns[0])
        db.insert(WorkflowStageDefinition(key="backlog", name="Backlog", is_builtin=True))
        definition = AppDefinition(
            key="example-erp",
            approved_by=actor.id,
            declaration={
                "schema_version": 1,
                "key": "example-erp",
                "name": "Example ERP",
                "description": "External issues",
                "version": "1.0.0",
                "capabilities": ["resources.read", "cards.create", "cards.presentation"],
                "resource_types": ["project"],
                "workflow_requirements": {"required": ["backlog"], "optional": []},
                "card_lifecycle": {kind: {"archive": {"mode": "retain", "description": "Retain source history."}, "restore": "retain_external_state"} for kind in ("issue", "task")},
            },
        )
        db.insert(definition)
        connection = AppConnection(app_key="example-erp", owner_id=actor.id, state="connected")
        db.insert(connection)
        binding = BoardAppBinding(
            project_id=project.id,
            app_key="example-erp",
            state="enabled",
            granted_capabilities=["resources.read", "cards.create", "cards.presentation"],
            workflow_mapping={"backlog": columns[0].get_uid()},
        )
        db.insert(binding)
        resource = AppResourceBinding(
            board_binding_id=binding.id,
            connection_id=connection.id,
            resource_type="project",
            external_resource_id="erp-project",
            is_selected=True,
            access_state="granted",
        )
        db.insert(resource)
    token = issue_connection_credential(actor, connection.id)["token"]
    return board, token, connection, binding, resource, definition, events


def call(creation, **kwargs):
    board, token, _, _, resource, *_ = creation
    return create_app_card(
        token,
        board[2].id,
        resource.id,
        "issue-1",
        kwargs.pop("title", "ERP issue"),
        description="External issue body",
        presentation=kwargs.pop("presentation", PRESENTATION),
        card_type=kwargs.pop("card_type", "issue"),
        **kwargs,
    )


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("policy", ["retain", "external", "missing"])
def test_native_archive_records_independent_receipt_and_rearchive(creation, monkeypatch, policy):
    from langboard_shared.domain.models import AppCardArchive, ProjectColumn
    from langboard_shared.domain.services import DomainService

    board, _, _, _, _, definition, _ = creation
    call(creation)
    with DbSession.atomic() as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.title == "ERP issue")).first()
        original_uid = card.get_uid()
        if policy == "missing":
            definition.declaration = {key: value for key, value in definition.declaration.items() if key != "card_lifecycle"}
        elif policy == "external":
            definition.declaration = {
                **definition.declaration,
                "card_lifecycle": {"issue": {
                    "archive": {"mode": "external", "action": "resolve_issue", "default_enabled": False,
                                "description": "Resolve only when explicitly enabled."},
                    "restore": "retain_external_state",
                }},
            }
        db.update(definition)
        archive = ProjectColumn(project_id=board[2].id, name="Archive", order=20, is_archive=True)
        db.insert(archive)
    monkeypatch.setattr(CardService, "notify_order_changed", lambda *args: None)
    service = DomainService()
    try:
        assert service.card.change_order(board[1], board[2], card, 0, archive)
        with DbSession.atomic() as db:
            stored = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
            receipt = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
            assert stored.archived_at is not None and stored.get_uid() == original_uid
            assert receipt.receipt["card_type"] == "issue"
            assert receipt.receipt["external_resource_id"] == "issue-1"
            assert receipt.receipt["external_action"] == "none"
            assert receipt.receipt["state"] == ("blocked" if policy == "missing" else "retained")
            assert receipt.receipt["reason"] == {
                "retain": "declared_retain", "external": "external_sync_disabled",
                "missing": "lifecycle_missing_or_ambiguous",
            }[policy]
            first_revision = receipt.card_revision
            first_archived_at = stored.archived_at
        # Reorder inside Archive does not create a new lifecycle operation.
        assert service.card.change_order(board[1], board[2], card, 0, archive)
        with DbSession.atomic() as db:
            assert len(db.exec(SqlBuilder.select.table(AppCardArchive)).all()) == 1
            assert db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().archived_at == first_archived_at
        assert service.card.change_order(board[1], board[2], card, 0, board[5][0])
        assert service.card.change_order(board[1], board[2], card, 0, archive)
        with DbSession.atomic() as db:
            receipts = db.exec(SqlBuilder.select.table(AppCardArchive)).all()
            assert len(receipts) == 2
            assert len({r.card_revision for r in receipts}) == 2
            assert first_revision in {r.card_revision for r in receipts}
    finally:
        service.close()


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_archive_receipt_failure_rolls_back_native_movement(creation, monkeypatch):
    import langboard_shared.domain.services.AppCardArchive as lifecycle
    from langboard_shared.domain.models import AppCardArchive, ProjectColumn
    from langboard_shared.domain.services import DomainService

    board = creation[0]
    call(creation)
    with DbSession.atomic() as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.title == "ERP issue")).first()
        original_column = card.project_column_id
        archive = ProjectColumn(project_id=board[2].id, name="Archive", order=20, is_archive=True)
        db.insert(archive)
    original = lifecycle.record_app_card_archive

    def fail_after_record(*args):
        original(*args)
        raise RuntimeError("Forced lifecycle persistence failure")

    monkeypatch.setattr(lifecycle, "record_app_card_archive", fail_after_record)
    monkeypatch.setattr(CardService, "notify_order_changed", lambda *args: pytest.fail("Failed archive cannot publish"))
    service = DomainService()
    try:
        with pytest.raises(RuntimeError, match="Forced lifecycle"):
            service.card.change_order(board[1], board[2], card, 0, archive)
        with DbSession.atomic() as db:
            stored = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
            assert stored.archived_at is None and stored.project_column_id == original_column
            assert db.exec(SqlBuilder.select.table(AppCardArchive)).all() == []
    finally:
        service.close()


def test_inbound_connection_and_resource_onboarding(creation):
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppConnectionManagement import (
        create_inbound_connection,
        disconnect_inbound_connection,
        select_inbound_resource,
    )
    from langboard_shared.domain.services.AppRegistry import AppRegistryConflict

    actor, project = creation[0][1:3]
    binding, definition = creation[3], creation[5]
    result = create_inbound_connection(actor, "example-erp", definition.edit_revision())
    connection_id = SnowflakeID.from_short_code(result["connection_uid"])
    service = DomainService().workflow_stage
    args = (service, actor, project.get_uid(), "example-erp", definition.edit_revision(),
            binding.get_uid(), binding.edit_revision(), connection_id, "project", "new-project")
    first = select_inbound_resource(*args)
    again = select_inbound_resource(*args, expected_access_revision=first["access_revision"])
    assert first == again and first["access_revision"] == 1
    with pytest.raises(AppRegistryConflict):
        select_inbound_resource(*args)
    removed = select_inbound_resource(*args, selected=False, expected_access_revision=1)
    assert removed["access_state"] == "revoked" and removed["access_revision"] == 2
    with pytest.raises(AppRegistryConflict):
        select_inbound_resource(*args, expected_access_revision=1)
    restored = select_inbound_resource(*args, expected_access_revision=2)
    assert restored["resource_uid"] == first["resource_uid"] and restored["access_revision"] == 3
    with DbSession.use(readonly=False) as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection_id)).first()
        assert connection.owner_id == actor.id and connection.credential_reference is None and connection.instance_url == ""
        assert len(db.exec(SqlBuilder.select.table(AppResourceBinding).where(AppResourceBinding.connection_id == connection_id)).all()) == 1
    with pytest.raises(AppRegistryConflict):
        disconnect_inbound_connection(actor, connection_id, "0" * 64)
    disconnected = disconnect_inbound_connection(actor, connection_id, result["revision"])
    assert disconnected["state"] == "disconnected"
    with pytest.raises(AppGovernanceDenied):
        issue_connection_credential(actor, connection_id)


def test_inbound_onboarding_current_authority(creation):
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppConnectionManagement import (
        create_inbound_connection,
        select_inbound_resource,
    )
    from langboard_shared.domain.services.AppRegistry import AppRegistryConflict

    actor, project = creation[0][1:3]
    binding, definition = creation[3], creation[5]
    with pytest.raises(AppRegistryConflict):
        create_inbound_connection(actor, "example-erp", "0" * 64)
    with pytest.raises(AppGovernanceDenied):
        create_inbound_connection(actor, "github", definition.edit_revision())
    result = create_inbound_connection(actor, "example-erp", definition.edit_revision())
    connection_id = SnowflakeID.from_short_code(result["connection_uid"])
    service = DomainService().workflow_stage
    args = (service, actor, project.get_uid(), "example-erp", definition.edit_revision(),
            binding.get_uid(), binding.edit_revision(), connection_id)
    with pytest.raises(ValueError):
        select_inbound_resource(*args, "undeclared", "new-project")
    with DbSession.atomic() as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == connection_id)).first()
        connection.owner_id = 2  # Another native user owns this personal connection.
        db.update(connection)
    with pytest.raises(AppGovernanceDenied):
        select_inbound_resource(*args, "project", "new-project")


def test_inbound_organization_authority_and_revocation_cleanup(creation):
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.models import AppGovernancePolicy, Organization
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppConnectionManagement import (
        create_inbound_connection,
        select_inbound_resource,
    )

    actor, project = creation[0][1:3]
    binding, definition = creation[3], creation[5]
    with DbSession.atomic() as db:
        organization = Organization(name="External organization", slug="external", owner_user_id=2)
        db.insert(organization)
    with pytest.raises(AppGovernanceDenied):
        create_inbound_connection(actor, "example-erp", definition.edit_revision(), organization.id)
    result = create_inbound_connection(actor, "example-erp", definition.edit_revision())
    service = DomainService().workflow_stage
    args = (service, actor, project.get_uid(), "example-erp", definition.edit_revision(),
            binding.get_uid(), binding.edit_revision(), SnowflakeID.from_short_code(result["connection_uid"]), "project", "cleanup")
    selected = select_inbound_resource(*args)
    with DbSession.atomic() as db:
        db.insert(AppGovernancePolicy(scope_key="global", mode="disabled"))
        definition.is_enabled = False
        db.update(definition)
    with pytest.raises(AppGovernanceDenied):
        create_inbound_connection(actor, "example-erp", definition.edit_revision())
    revoked_args = (*args[:4], definition.edit_revision(), *args[5:])
    removed = select_inbound_resource(*revoked_args, selected=False, expected_access_revision=selected["access_revision"])
    assert not removed["selected"] and removed["access_state"] == "revoked"


def test_inbound_receipt_listing_scope_pagination_and_disabled_cleanup(creation):
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.models import AppGovernancePolicy, Organization
    from langboard_shared.domain.services.AppConnectionManagement import (
        create_inbound_connection,
        disconnect_inbound_connection,
        list_inbound_connections,
    )

    actor, definition = creation[0][1], creation[5]
    created = create_inbound_connection(actor, "example-erp", definition.edit_revision())
    with DbSession.atomic() as db:
        organization = Organization(name="Managed organization", slug="managed", owner_user_id=actor.id)
        db.insert(organization)
        db.insert(AppConnection(app_key="example-erp", owner_id=2, state="connected"))
    org_receipt = create_inbound_connection(actor, "example-erp", definition.edit_revision(), organization.id)
    first = list_inbound_connections(actor, "example-erp", limit=1)
    assert first["items"][0]["connection_uid"] == creation[2].get_uid()
    assert first["next_cursor"] == creation[2].get_uid()
    second = list_inbound_connections(actor, "example-erp", after_id=creation[2].id, limit=1)
    assert second["items"] == [created] and second["next_cursor"] is None
    assert list_inbound_connections(actor, "example-erp", organization_id=organization.id)["items"] == [org_receipt]
    with DbSession.atomic() as db:
        organization.owner_user_id = 2
        db.update(organization)
        db.insert(AppGovernancePolicy(scope_key="global", mode="disabled"))
        definition.is_enabled = False
        db.update(definition)
    with pytest.raises(AppGovernanceDenied):
        list_inbound_connections(actor, "example-erp", organization_id=organization.id)
    receipt = list_inbound_connections(actor, "example-erp", after_id=creation[2].id)["items"][0]
    disconnected = disconnect_inbound_connection(
        actor, SnowflakeID.from_short_code(receipt["connection_uid"]), receipt["revision"]
    )
    assert list_inbound_connections(actor, "example-erp", after_id=creation[2].id)["items"][0] == {
        **created,
        **disconnected,
    }
    with pytest.raises(ValueError):
        list_inbound_connections(actor, "example-erp", limit=51)


@pytest.mark.parametrize("mutation", ["state", "credential_reference", "reconnected"])
def test_inbound_revision_rejects_mutated_identity(creation, mutation):
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.services.AppConnectionAuthentication import (
        _identity_hash,
        authenticate_connection_credential,
    )
    from langboard_shared.domain.services.AppConnectionManagement import (
        create_inbound_connection,
        disconnect_inbound_connection,
        list_inbound_connections,
    )
    from langboard_shared.domain.services.AppRegistry import AppRegistryConflict

    actor, definition = creation[0][1], creation[5]
    receipt = create_inbound_connection(actor, "example-erp", definition.edit_revision())
    identifier = SnowflakeID.from_short_code(receipt["connection_uid"])
    with DbSession.atomic() as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == identifier)).first()
        assert _identity_hash(db, connection) == receipt["revision"]
    issued = issue_connection_credential(actor, identifier)
    assert authenticate_connection_credential(issued["token"]).connection_id == identifier
    with DbSession.atomic() as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == identifier)).first()
        if mutation == "credential_reference":
            connection.credential_reference = "host-secret-reference"
        else:
            connection.state = "disconnected"
        db.update(connection)
    if mutation == "reconnected":
        with DbSession.atomic() as db:
            connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == identifier)).first()
            connection.state = "connected"
            db.update(connection)
    with pytest.raises(AppGovernanceDenied):
        authenticate_connection_credential(issued["token"])
    expected = AppGovernanceDenied if mutation == "credential_reference" else AppRegistryConflict
    with pytest.raises(expected):
        disconnect_inbound_connection(actor, identifier, receipt["revision"])
    if mutation != "credential_reference":
        current = list_inbound_connections(actor, "example-erp", after_id=creation[2].id)["items"][0]
        assert current["revision"] != receipt["revision"]
        disconnected = disconnect_inbound_connection(actor, identifier, current["revision"])
        assert list_inbound_connections(actor, "example-erp", after_id=creation[2].id)["items"][0] == disconnected | {
            "ownership": "personal",
        }


def test_native_backlog_presentation_and_replay(creation):
    first = call(creation)
    again = call(creation)
    assert first["created"] and not again["created"]
    assert first["card_uid"] == again["card_uid"] and again["effects_state"] == "dispatched"
    assert creation[-1] == [first["card_uid"]]
    with DbSession.use(readonly=False) as db:
        cards = db.exec(SqlBuilder.select.table(Card)).all()
        assert len(cards) == 1 and cards[0].project_column_id == creation[0][5][0].id
        metadata = db.exec(SqlBuilder.select.table(CardMetadata)).first()
        assert loads(metadata.value) == PRESENTATION
        assert len(db.exec(SqlBuilder.select.table(ExternalImportRecord)).all()) == 1
        assert loads(db.exec(SqlBuilder.select.table(ExternalImportRecord)).first().provenance)["card_type"] == "issue"
        owners = db.exec(SqlBuilder.select.table(CardAppOwnership)).all()
        selections = db.exec(SqlBuilder.select.table(CardAppResourceSelection)).all()
        assert len(owners) == len(selections) == 1
        owner, selection = owners[0], selections[0]
        assert owner.card_id == cards[0].id and owner.app_key == "example-erp" and owner.revision == 1
        assert selection.card_id == cards[0].id and selection.connection_id == creation[2].id
        assert selection.resource_uids == [creation[4].get_uid()]
        assert len(db.exec(SqlBuilder.select.table(CardAppOwnershipAudit)).all()) == 1
        assert len(db.exec(SqlBuilder.select.table(CardAppResourceSelectionAudit)).all()) == 1
    with pytest.raises(AppCardCreationConflict):
        call(creation, title="Changed payload")
    with pytest.raises(AppCardCreationConflict):
        call(creation, card_type="task")


@pytest.mark.parametrize("kind", [None, "undeclared"])
def test_undeclared_card_type_cannot_create(creation, kind):
    with pytest.raises(AppGovernanceDenied):
        call(creation, card_type=kind)
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(Card)).all()
        assert not db.exec(SqlBuilder.select.table(ExternalImportRecord)).all()


@pytest.mark.parametrize("revoke", ["resource", "grant", "definition", "column", "shared_personal", "lifecycle"])
def test_current_scope_rechecked_before_replay(creation, revoke):
    call(creation)
    board, _, _, binding, resource, definition, _ = creation
    with DbSession.atomic() as db:
        if revoke == "resource":
            resource.is_selected = False
            db.update(resource)
        elif revoke == "grant":
            binding.granted_capabilities = []
            db.update(binding)
        elif revoke == "definition":
            definition.is_enabled = False
            db.update(definition)
        elif revoke == "column":
            board[5][0].workflow_stage = "active"
            db.update(board[5][0])
        elif revoke == "lifecycle":
            definition.declaration = {key: value for key, value in definition.declaration.items() if key != "card_lifecycle"}
            db.update(definition)
        else:
            db.insert(ProjectAssignedUser(project_id=board[2].id, user_id=2))
    with pytest.raises(AppGovernanceDenied):
        call(creation)
    assert len(creation[-1]) == 1


def test_foreign_presentation_namespace_cannot_claim_identity(creation):
    with pytest.raises(AppGovernanceDenied):
        call(creation, presentation={**PRESENTATION, "key": "app.other.issue"})
    assert creation[-1] == []


def test_created_app_card_can_request_interaction_with_its_retained_resources(creation):
    from datetime import timedelta
    from langboard_shared.core.types import SafeDateTime, SnowflakeID
    from langboard_shared.domain.services.AppCardInteractions import create_app_card_interaction

    board, token, _, binding, _, definition, _ = creation
    with DbSession.atomic() as db:
        definition.declaration = {**definition.declaration, "capabilities": [*definition.declaration["capabilities"], "interactions.request"]}
        binding.granted_capabilities = [*binding.granted_capabilities, "interactions.request"]
        db.update(definition)
        db.update(binding)
    card = call(creation)
    request = {"schema_version": 1, "kind": "action", "app_request_key": "review-issue",
               "prompt": {"en": "Review the issue"},
               "questions": [{"id": "next", "prompt": {"en": "Choose the next step"}, "type": "single_choice", "choices": [{"id": "hold", "label": {"en": "Hold"}}]}],
               "presentation": {"type": "choice_card"}, "expires_at": (SafeDateTime.now() + timedelta(hours=1)).isoformat()}
    result = create_app_card_interaction(token, board[2].id, SnowflakeID.from_short_code(card["card_uid"]), request)
    assert result["state"] == "pending" and result["execution_resumed"] is False
    with DbSession.atomic() as db:
        binding.granted_capabilities = [scope for scope in binding.granted_capabilities if scope != "interactions.request"]
        db.update(binding)
    with pytest.raises(AppGovernanceDenied):
        create_app_card_interaction(token, board[2].id, SnowflakeID.from_short_code(card["card_uid"]), request)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_independent_monitor_creates_owned_card_then_requests_hitl_over_http(creation, monkeypatch):
    """Ownership comes from the public creation transaction, never a fixture row."""
    import asyncio
    from datetime import timedelta
    from types import SimpleNamespace
    import langboard.routes.settings.AppCardCreationApi  # noqa: F401
    import langboard.routes.settings.AppCardInteractionApi  # noqa: F401
    import langboard.routes.settings.AppCardReadApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard_sdk import AppCards, HttpTransport, NativeApiError
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.types import SafeDateTime, SnowflakeID
    from packaged_monitor import load_monitor

    Monitor = load_monitor(monkeypatch, "example-monitor-hitl-provenance.json")
    board, _, connection, binding, resource, definition, events = creation
    with DbSession.atomic() as db:
        connection.app_key = definition.key = binding.app_key = "example-monitor"
        definition.declaration = {**definition.declaration, "key": "example-monitor",
            "capabilities": [*definition.declaration["capabilities"], "interactions.request"]}
        binding.granted_capabilities = [*binding.granted_capabilities, "interactions.request"]
        for row in (connection, definition, binding):
            db.update(row)
    token = issue_connection_credential(board[1], connection.id)["token"]
    app = FastAPI()
    app.include_router(AppRouter.api)
    with TestClient(app) as client:
        async def send(method, path, **kwargs):
            return client.request(method, path, headers={"Authorization": "Bearer " + token}, **kwargs)
        transport = HttpTransport(SimpleNamespace(request=send))
        creation_path = f"/apps/v1/boards/{board[2].get_uid()}/cards"
        body = {"resource_uid": resource.get_uid(), "external_id": "monitor-owned-issue",
                "title": "Independent issue requiring a human choice", "card_type": "issue"}
        created = asyncio.run(transport.request("POST", creation_path, json=body))
        assert created["created"] is True
        replay = asyncio.run(transport.request("POST", creation_path, json=body))
        assert replay["created"] is False and replay["card_uid"] == created["card_uid"]
        read_path = creation_path + "/" + created["card_uid"]
        assert client.get(read_path).status_code == 401
        assert client.get(read_path, headers={"Authorization": "Bearer invalid"}).status_code == 401
        reader = AppCards(transport)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))
        assert denied.value.status_code == 403
        from langboard_shared.core.db import EditorContentModel
        with DbSession.atomic() as db:
            definition.declaration = {**definition.declaration, "capabilities": [*definition.declaration["capabilities"], "cards.read"]}
            binding.granted_capabilities = [*binding.granted_capabilities, "cards.read"]
            db.update(definition)
            db.update(binding)
            native_card = db.exec(SqlBuilder.select.table(Card).where(Card.id == SnowflakeID.from_short_code(created["card_uid"]))).first()
            native_card.description = EditorContentModel(content="한글 current content")
            db.update(native_card)
        current_card = asyncio.run(reader.read(board[2].get_uid(), created["card_uid"], limit=2))
        assert current_card["title"] == body["title"]
        assert current_card["execution_generation"] == 0
        assert current_card["description"] == {"content": "한글", "format": "markdown", "total_chars": 18, "next_offset": 2}
        assert current_card["resources"][0]["resource_uid"] == resource.get_uid()
        assert current_card["ownership_revision"] == current_card["selection_revision"] == 1
        assert "credential" not in str(current_card) and token not in str(current_card)
        next_page = asyncio.run(reader.read(board[2].get_uid(), created["card_uid"], offset=2))
        assert next_page["description"]["content"] == " current content"
        with DbSession.atomic() as db:
            resource.access_state = "revoked"
            db.update(resource)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))
        assert denied.value.status_code == 403
        with DbSession.atomic() as db:
            resource.access_state = "granted"
            native_card.visibility = "PRIVATE"
            native_card.owner_user_id = board[1].id
            db.update(resource)
            db.update(native_card)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))
        assert denied.value.status_code == 403
        with DbSession.atomic() as db:
            native_card.visibility = "INTERNAL"
            native_card.owner_user_id = None
            db.update(native_card)
        with DbSession.atomic() as db:
            current_owner = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == native_card.id)).first()
            current_owner.app_key = "different-app"
            db.update(current_owner)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))
        assert denied.value.status_code == 403
        with DbSession.atomic() as db:
            current_owner.app_key = "example-monitor"
            native_card.archived_at = SafeDateTime.now()
            db.update(current_owner)
            db.update(native_card)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))
        assert denied.value.status_code == 403
        with DbSession.atomic() as db:
            native_card.archived_at = None
            db.update(native_card)
        request = {"schema_version": 1, "kind": "question", "app_request_key": "monitor-triage",
            "prompt": {"en": "Choose the next step"},
            "questions": [{"id": "next", "prompt": {"en": "Next step"}, "type": "single_choice",
                           "choices": [{"id": "hold", "label": {"en": "Hold"}}]}],
            "presentation": {"type": "choice_card"},
            "expires_at": (SafeDateTime.now() + timedelta(hours=1)).isoformat()}
        first = asyncio.run(Monitor.question(transport, board[2].get_uid(), created["card_uid"], request))
        repeated = asyncio.run(Monitor.question(transport, board[2].get_uid(), created["card_uid"], request))
        assert first["state"] == "pending" and first["execution_resumed"] is False
        assert repeated["changed"] is False and repeated["interaction_uid"] == first["interaction_uid"]
        current = asyncio.run(Monitor.current_response(transport, board[2].get_uid(), created["card_uid"], first["interaction_uid"]))
        assert current["request"] == request
        with DbSession.atomic() as db:
            card_id = SnowflakeID.from_short_code(created["card_uid"])
            ownership = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card_id)).first()
            assert ownership.app_key == "example-monitor" and ownership.revision == 1
            assert len(db.exec(SqlBuilder.select.table(CardAppOwnershipAudit).where(CardAppOwnershipAudit.card_id == card_id)).all()) == 1
            selection = db.exec(SqlBuilder.select.table(CardAppResourceSelection).where(CardAppResourceSelection.card_id == card_id)).first()
            assert selection.resource_uids == [resource.get_uid()] and selection.connection_id == connection.id
            binding.granted_capabilities = [value for value in binding.granted_capabilities if value != "interactions.request"]
            db.update(binding)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(Monitor.current_response(transport, board[2].get_uid(), created["card_uid"], first["interaction_uid"]))
        assert denied.value.status_code == 403
        # Read consent is independent of the withdrawn HITL write capability.
        assert asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))["title"] == body["title"]
        with DbSession.atomic() as db:
            binding.granted_capabilities = [value for value in binding.granted_capabilities if value != "cards.read"]
            db.update(binding)
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(reader.read(board[2].get_uid(), created["card_uid"]))
        assert denied.value.status_code == 403
        assert len(events) == 1


def test_concurrent_postgres_replays_create_one_card(creation):
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock proof")
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: call(creation), range(4)))
    assert len({r["card_uid"] for r in receipts}) == 1
    assert sum(r["created"] for r in receipts) == 1
    assert len(creation[-1]) == 1


def test_http_create_replay_conflict_and_authentication(creation):
    import langboard.routes.settings.AppCardCreationApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard_shared.core.routing import AppRouter

    app = FastAPI()
    app.include_router(AppRouter.api)
    board, token, _, _, resource, *_ = creation
    url = f"/apps/v1/boards/{board[2].get_uid()}/cards"
    body = {
        "resource_uid": resource.get_uid(),
        "external_id": "http-issue",
        "title": "ERP HTTP issue",
        "description": "Issue body",
        "presentation": PRESENTATION,
        "card_type": "issue",
    }
    with TestClient(app) as client:
        assert client.post(url, json=body).status_code == 401
        headers = {"Authorization": "Bearer " + token}
        assert client.post(url, json={key: value for key, value in body.items() if key != "card_type"}, headers=headers).status_code == 403
        assert client.post(url, json={**body, "card_type": "undeclared"}, headers=headers).status_code == 403
        first = client.post(url, json=body, headers=headers)
        assert first.status_code == 201, first.text
        again = client.post(url, json=body, headers=headers)
        assert again.status_code == 200 and again.json()["card_uid"] == first.json()["card_uid"]
        assert client.post(url, json={**body, "title": "changed"}, headers=headers).status_code == 409
        assert client.post(url, json={**body, "visibility": "SHARED"}, headers=headers).status_code == 400


def test_effect_failure_retains_one_committed_card_and_no_implicit_retry(creation, monkeypatch):
    def fail(*_):
        raise RuntimeError("private broker credentials must never enter receipts")

    monkeypatch.setattr(CardService, "dispatch_created", fail)
    first = call(creation)
    again = call(creation)
    assert again["card_uid"] == first["card_uid"] and again["effects_state"] == "failed"
    with DbSession.use(readonly=False) as db:
        receipt = db.exec(SqlBuilder.select.table(ExternalImportRecord)).first()
        assert receipt.effects_attempts == 1
        assert "private broker" not in receipt.effects_error
        card = db.exec(SqlBuilder.select.table(Card)).first()
        assert card.visibility == "INTERNAL" and card.owner_user_id is None


def test_presentation_failure_rolls_back_card_and_source_receipt(creation, monkeypatch):
    from langboard_shared.domain.services.factory.MetadataService import MetadataService

    def fail(*_):
        raise ValueError("Rejected presentation persistence")

    monkeypatch.setattr(MetadataService, "save_card", fail)
    with pytest.raises(ValueError, match="Rejected presentation"):
        call(creation)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(Card)).all() == []
        assert db.exec(SqlBuilder.select.table(ExternalImportRecord)).all() == []
        assert db.exec(SqlBuilder.select.table(CardAppOwnership)).all() == []
        assert db.exec(SqlBuilder.select.table(CardAppOwnershipAudit)).all() == []
        assert db.exec(SqlBuilder.select.table(CardAppResourceSelection)).all() == []
        assert db.exec(SqlBuilder.select.table(CardAppResourceSelectionAudit)).all() == []
    assert creation[-1] == []


def test_organization_connection_can_create_in_shared_organization_board(creation):
    from langboard_shared.domain.models import Organization

    board, _, connection, _, _, _, _ = creation
    with DbSession.atomic() as db:
        organization = Organization(name="Example organization", slug="example", owner_user_id=board[1].id)
        db.insert(organization)
        board[2].organization_id = organization.id
        db.update(board[2])
        db.insert(ProjectAssignedUser(project_id=board[2].id, user_id=2))
        connection.ownership = "organization"
        connection.organization_id = organization.id
        db.update(connection)
    token = issue_connection_credential(board[1], connection.id)["token"]
    updated = (board, token, *creation[2:])
    assert call(updated)["created"]
    assert not call(updated)["created"]


def test_explicit_board_consent_enables_then_revokes_creation(creation, monkeypatch):
    from langboard_shared.domain.services.AppRegistry import AppRegistryConflict, set_board_consent
    from langboard_shared.publishers import AppSettingPublisher

    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    board, _, _, binding, _, definition, _ = creation
    with DbSession.atomic() as db:
        binding.granted_capabilities = []
        binding.state = "disabled"
        db.update(binding)
    with pytest.raises(AppGovernanceDenied):
        call(creation)
    result = set_board_consent(
        board[0],
        board[1],
        board[2].get_uid(),
        "example-erp",
        definition.edit_revision(),
        binding.get_uid(),
        binding.edit_revision(),
        ["resources.read", "cards.create", "cards.presentation"],
    )
    assert result["state"] == "enabled" and not result["stage_transitions_enabled"]
    assert call(creation)["created"]
    with pytest.raises(AppRegistryConflict):
        set_board_consent(
            board[0],
            board[1],
            board[2].get_uid(),
            "example-erp",
            definition.edit_revision(),
            binding.get_uid(),
            binding.edit_revision(),
            [],
        )
    result = set_board_consent(
        board[0],
        board[1],
        board[2].get_uid(),
        "example-erp",
        definition.edit_revision(),
        binding.get_uid(),
        result["revision"],
        [],
    )
    assert result["state"] == "disabled"
    with pytest.raises(AppGovernanceDenied):
        call(creation)


def test_board_consent_cannot_exceed_declaration_or_stale_app_review(creation):
    from langboard_shared.domain.services.AppRegistry import AppRegistryConflict, set_board_consent

    board, _, _, binding, _, definition, _ = creation
    args = (
        board[0],
        board[1],
        board[2].get_uid(),
        "example-erp",
        definition.edit_revision(),
        binding.get_uid(),
        binding.edit_revision(),
    )
    with pytest.raises(ValueError):
        set_board_consent(*args, ["execution.run"])
    with pytest.raises(ValueError):
        set_board_consent(*args, ["cards.create", "cards.create"])
    args = (*args[:4], "0" * 64, *args[5:])
    with pytest.raises(AppRegistryConflict):
        set_board_consent(*args, ["cards.create"])


def test_board_consent_requires_current_management_role(creation):
    from langboard_shared.domain.services.AppRegistry import AppRegistryDenied, set_board_consent

    board, _, _, binding, _, definition, _ = creation
    with DbSession.atomic() as db:
        board[2].owner_id = 2
        db.update(board[2])
        board[4].actions = ["read"]
        db.update(board[4])
    with pytest.raises(AppRegistryDenied):
        set_board_consent(
            board[0],
            board[1],
            board[2].get_uid(),
            "example-erp",
            definition.edit_revision(),
            binding.get_uid(),
            binding.edit_revision(),
            ["cards.create"],
        )


def test_policy_disable_blocks_new_consent_but_allows_explicit_removal(creation, monkeypatch):
    from langboard_shared.domain.models import AppGovernancePolicy
    from langboard_shared.domain.services.AppRegistry import AppRegistryDenied, set_board_consent
    from langboard_shared.publishers import AppSettingPublisher

    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    board, _, _, binding, _, definition, _ = creation
    with DbSession.atomic() as db:
        db.insert(AppGovernancePolicy(scope_key="global", mode="disabled"))
    args = (
        board[0],
        board[1],
        board[2].get_uid(),
        "example-erp",
        definition.edit_revision(),
        binding.get_uid(),
        binding.edit_revision(),
    )
    with pytest.raises(AppRegistryDenied):
        set_board_consent(*args, ["cards.create"])
    assert set_board_consent(*args, [])["state"] == "disabled"


def test_inbound_resource_management_snapshot_scope_and_cleanup(creation):
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppConnectionManagement import list_inbound_resources

    actor, project, other = creation[0][1:4]
    connection, binding, resource, definition = creation[2:6]
    service = DomainService().workflow_stage
    args = (service, actor, project.get_uid(), "example-erp", connection.id)
    snapshot = list_inbound_resources(*args, limit=1)
    assert snapshot["app_revision"] == definition.edit_revision()
    assert snapshot["binding_revision"] == binding.edit_revision()
    assert snapshot["resource_types"] == ["project"]
    assert snapshot["items"][0]["resource_uid"] == resource.get_uid()
    assert "credential_reference" not in str(snapshot)
    with pytest.raises(AppGovernanceDenied):
        list_inbound_resources(service, other, project.get_uid(), "example-erp", connection.id)
    with pytest.raises(ValueError):
        list_inbound_resources(*args, limit=51)
    with DbSession.atomic() as db:
        definition.is_enabled = False
        db.update(definition)
        resource.is_selected = False
        resource.access_state = "revoked"
        resource.access_revision += 1
        db.update(resource)
    assert list_inbound_resources(*args)["items"][0]["selected"] is False
    assert list_inbound_resources(*args)["items"][0]["access_revision"] == resource.access_revision
