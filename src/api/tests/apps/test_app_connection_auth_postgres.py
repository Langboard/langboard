"""Committed revocation fences cached native sessions and retains execution evidence."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    AppConnection,
    AppDefinition,
    AppExecutionOutbox,
    AppExecutionRequest,
    AppGovernancePolicy,
)
from langboard_shared.domain.services.AppConnectionAuthentication import (
    authenticate_connection_credential,
    issue_connection_credential,
)
from langboard_shared.domain.services.AppConnectionResources import list_connection_resources
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from sqlalchemy import update
from test_app_connection_resources import scope
from test_app_event_delivery import delivery_scope


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("revoked", ["policy", "definition", "connection"])
def test_cached_authentication_rechecks_committed_revocation(board, revoked):
    connection, definition, _, _, token = scope(board)
    engine = DbEngine.get_main_engine()
    with DbSession.atomic() as db:
        db.insert(AppGovernancePolicy(scope_key="global", mode="approved_only"))
    assert list_connection_resources(token, board[2].id)["items"]
    with DbSession.atomic() as db:
        # Keep identities alive as a composed native operation can. SQL queries
        # alone do not refresh attributes on already-loaded ORM identities.
        cached_policy = db.exec(SqlBuilder.select.table(AppGovernancePolicy)).first()
        cached_definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        cached_connection = db.exec(SqlBuilder.select.table(AppConnection)).first()
        assert authenticate_connection_credential(token).connection_id == connection.id
        with engine.begin() as writer:
            if revoked == "policy":
                writer.execute(update(AppGovernancePolicy).values(mode="disabled", generation=1))
            elif revoked == "definition":
                writer.execute(update(AppDefinition).where(AppDefinition.id == definition.id).values(is_enabled=False))
            else:
                writer.execute(
                    update(AppConnection).where(AppConnection.id == connection.id).values(state="disconnected")
                )
        with pytest.raises(AppGovernanceDenied):
            authenticate_connection_credential(token)
        with pytest.raises(AppGovernanceDenied):
            issue_connection_credential(board[1], connection.id)
        with pytest.raises(AppGovernanceDenied):
            list_connection_resources(token, board[2].id)
        assert cached_policy is not None and cached_definition is not None and cached_connection is not None


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("revoked", ["policy", "connection"])
def test_pending_dispatch_rechecks_revocation_and_preserves_unknown_history(board, monkeypatch, revoked):
    from langboard_shared.domain.services.AppEventDelivery import claim_app_event

    connection, _, event = delivery_scope(board, monkeypatch)
    engine = DbEngine.get_main_engine()
    with DbSession.atomic() as db:
        db.insert(AppGovernancePolicy(scope_key="global", mode="approved_only"))
        pending = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
        # A prior expired delivery attempt has unknown external outcome.
        pending.delivery_history = [{"attempt": 1, "outcome": "lease_expired", "at": "2026-10-01T00:00:00Z"}]
        pending.attempt_count = 1
        db.update(pending)
        original_request = db.exec(SqlBuilder.select.table(AppExecutionRequest)).first()
        original_authority = dict(original_request.authority)
        request_id = original_request.id
    with DbSession.atomic() as db:
        cached_policy = db.exec(SqlBuilder.select.table(AppGovernancePolicy)).first()
        with engine.begin() as writer:
            if revoked == "policy":
                writer.execute(update(AppGovernancePolicy).values(mode="disabled", generation=1))
            else:
                writer.execute(
                    update(AppConnection).where(AppConnection.id == connection.id).values(state="disconnected")
                )
        assert claim_app_event(event.id, 1) is None
        assert cached_policy is not None
    with DbSession.atomic() as db:
        retained = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
        request = db.exec(SqlBuilder.select.table(AppExecutionRequest)).first()
        assert retained.state == "blocked" and retained.attempt_count == 1
        assert retained.claim_token is None
        assert retained.delivery_history == [{"attempt": 1, "outcome": "lease_expired", "at": "2026-10-01T00:00:00Z"}]
        assert request.id == request_id and request.authority == original_authority


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_cached_organization_policy_revocation_fences_connection(board):
    from langboard_shared.domain.models import Organization
    from langboard_shared.domain.services.AppGovernance import current_policy

    connection, _, _, _, _ = scope(board)
    engine = DbEngine.get_main_engine()
    with DbSession.atomic() as db:
        organization = Organization(name="Revocation scope", slug="revocation-scope", owner_user_id=board[1].id)
        db.insert(organization)
        connection.ownership = "organization"
        connection.organization_id = organization.id
        board[2].organization_id = organization.id
        db.update(connection)
        db.update(board[2])
        db.insert(
            AppGovernancePolicy(
                scope_key=f"organization:{organization.id}", organization_id=organization.id, mode="approved_only"
            )
        )
    token = issue_connection_credential(board[1], connection.id)["token"]
    with DbSession.atomic() as db:
        cached = db.exec(SqlBuilder.select.table(AppGovernancePolicy)).first()
        assert current_policy(db, organization.id)["effective_mode"] == "approved_only"
        assert authenticate_connection_credential(token).organization_id == organization.id
        with engine.begin() as writer:
            writer.execute(
                update(AppGovernancePolicy)
                .where(AppGovernancePolicy.organization_id == organization.id)
                .values(mode="disabled", generation=1)
            )
        with pytest.raises(AppGovernanceDenied):
            authenticate_connection_credential(token)
        assert current_policy(db, organization.id)["effective_mode"] == "disabled"
        assert cached is not None
