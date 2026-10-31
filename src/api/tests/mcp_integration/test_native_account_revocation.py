"""Account reactivation cannot restore previously revoked native MCP board consent."""

from unittest.mock import Mock
import pytest
from fastmcp.exceptions import AuthorizationError
from langboard.mcp_integration.NativeConsent import native_call_boundary
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.NativeMcpConsent import (
    connect_native_client,
    effective_board_ids,
    list_native_connections,
    read_native_board_consent,
    save_native_board_consent,
)
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from test_native_board_consent import native, native_engine  # noqa: F401
from test_role_concurrency import run_race


@pytest.fixture
def revoked_account(native, monkeypatch):  # noqa: F811
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    for publisher, names in ((UserPublisher, ("updated", "deactivated")), (AppSettingPublisher, ("user_updated",))):
        for name in names:
            monkeypatch.setattr(publisher, name, Mock())
    engine, user, other, _ = native
    connection = connect_native_client(user, "one-client-two-boards")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    save_native_board_consent(user, connection.id, 20, ["read"], None)
    other_connection = connect_native_client(other, "unaffected-client")
    save_native_board_consent(other, other_connection.id, 10, ["read"], None)
    return engine, user, other, connection, other_connection, DomainService()


@pytest.mark.parametrize("entrypoint", ["administrator", "scim"])
def test_reactivation_requires_new_explicit_board_consent(revoked_account, monkeypatch, entrypoint):
    _, user, other, connection, other_connection, service = revoked_account
    assert effective_board_ids(user, connection.id, "read") == {10, 20}
    if entrypoint == "scim":
        # Directory membership reconciliation remains the existing SCIM owner's job.
        monkeypatch.setattr(service.scim_provisioning, "_bound_projects_for_user", lambda _: [])
        monkeypatch.setattr(service.scim_provisioning.repo.scim_group_member, "delete_all_by_user", Mock())
        service.scim_provisioning.deactivate_user(user)
        service.scim_provisioning.repo.scim_group_member.delete_all_by_user.assert_called_once_with(user)
    else:
        assert service.user.update(user, {"activated_at": None}, from_setting=True)
    with pytest.raises(AppGovernanceDenied):
        effective_board_ids(user, connection.id, "read")
    assert service.user.update(user, {"activated_at": SafeDateTime.now()}, from_setting=True)
    assert effective_board_ids(user, connection.id, "read") == frozenset()
    assert effective_board_ids(user, connection.id, "write") == frozenset()
    assert list_native_connections(user) == [{"uid": connection.get_uid(), "client_id": connection.client_id}]
    for board in (10, 20):
        receipt = read_native_board_consent(user, connection.id, board)
        assert receipt["scopes"] == [] and receipt["revision"] == 2
        with pytest.raises(AppGovernanceConflict):
            save_native_board_consent(user, connection.id, board, ["read"], 1)
    save_native_board_consent(user, connection.id, 10, ["read"], 2)
    assert effective_board_ids(user, connection.id, "read") == {10}
    assert effective_board_ids(user, connection.id, "write") == frozenset()
    assert effective_board_ids(other, other_connection.id, "read") == {10}


def test_rolled_back_deactivation_preserves_native_consent(revoked_account):
    _, user, _, connection, _, service = revoked_account
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            service.user.update(user, {"activated_at": None}, from_setting=True)
            raise RuntimeError("rollback")
    assert effective_board_ids(user, connection.id, "read") == {10, 20}
    assert read_native_board_consent(user, connection.id, 10)["revision"] == 1
    assert user.auth_version == 0
    UserPublisher.deactivated.assert_not_called()


async def test_existing_oauth_connection_is_denied_at_native_call_boundary_after_restore(revoked_account):
    _, user, _, connection, _, service = revoked_account
    arguments = {"project_uid": SnowflakeID(10).to_short_code(), "card_uid": SnowflakeID(210).to_short_code()}
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id})
    try:
        async with native_call_boundary("get_card_bundle", arguments):
            pass
        assert service.user.update(user, {"activated_at": None}, from_setting=True)
        assert service.user.update(user, {"activated_at": SafeDateTime.now()}, from_setting=True)
        with pytest.raises(AuthorizationError):
            async with native_call_boundary("get_card_bundle", arguments):
                pytest.fail("Old OAuth connection restored revoked board access")
        save_native_board_consent(user, connection.id, 10, ["read"], 2)
        async with native_call_boundary("get_card_bundle", arguments):
            pass
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("operation", ["connect", "consent"])
@pytest.mark.parametrize("account_first", [True, False])
def test_native_grant_mutations_serialize_with_account_deactivation(revoked_account, operation, account_first):
    engine, user, _, connection, _, service = revoked_account
    if engine.dialect.name != "postgresql":
        pytest.skip("SQLite does not support row locks; race cases run on PostgreSQL")

    def deactivate():
        return service.user.update(user, {"activated_at": None}, from_setting=True)

    def grant():
        try:
            if operation == "connect":
                return connect_native_client(user, "racing-client")
            return save_native_board_consent(user, connection.id, 10, ["read", "write"], 1)
        except AppGovernanceDenied:
            return "denied"

    first, second = run_race(
        engine, User, deactivate if account_first else grant, grant if account_first else deactivate
    )
    assert (first if account_first else second) is True
    assert (second == "denied") if account_first else first
    assert service.user.update(user, {"activated_at": SafeDateTime.now()}, from_setting=True)
    assert effective_board_ids(user, connection.id, "read") == frozenset()
