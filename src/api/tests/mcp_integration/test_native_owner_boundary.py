"""Selected native owner consent restores the enclosing SQL authority scope."""

import pytest
from fastmcp.exceptions import AuthorizationError
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.services.NativeMcpBoundary import native_mcp_boundary


@pytest.mark.parametrize("owner_fails", [False, True])
def test_native_owner_boundary_restores_outer_scope(monkeypatch, owner_fails):
    from contextlib import nullcontext
    from langboard.mcp_integration import NativeConsent

    outer, selected = {"outer": True}, {"selected": True}
    auth = {"transport": "oauth", "user_or_bot": object()}
    arguments, calls = {"project_uid": "reviewed"}, []

    def scope(current_auth, command, payload):
        calls.append((current_auth, command, payload))
        return selected

    monkeypatch.setattr(NativeConsent, "_scope", scope)
    auth_token, scope_token = mcp_auth_context.set(auth), native_mcp_boundary.set(outer)
    try:
        with pytest.raises(RuntimeError, match="owner failed") if owner_fails else nullcontext():
            with NativeConsent.native_owner_boundary("append_wiki_content", arguments):
                assert native_mcp_boundary.get() is selected
                if owner_fails:
                    raise RuntimeError("owner failed")
        assert native_mcp_boundary.get() is outer
        assert calls == [(auth, "append_wiki_content", arguments)]
    finally:
        native_mcp_boundary.reset(scope_token)
        mcp_auth_context.reset(auth_token)


def test_native_owner_boundary_denial_keeps_outer_scope(monkeypatch):
    from langboard.mcp_integration import NativeConsent

    outer = {"outer": True}

    def denied(*_args):
        raise AuthorizationError("Selected command denied")

    monkeypatch.setattr(NativeConsent, "_scope", denied)
    auth_token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": object()})
    scope_token = native_mcp_boundary.set(outer)
    try:
        with pytest.raises(AuthorizationError, match="Selected command denied"):
            with NativeConsent.native_owner_boundary("convert_wiki_selection_to_secret", {}):
                pytest.fail("Denied owner must not run")
        assert native_mcp_boundary.get() is outer
    finally:
        native_mcp_boundary.reset(scope_token)
        mcp_auth_context.reset(auth_token)


@pytest.mark.parametrize("auth", [None, {"transport": "api"}])
def test_native_owner_boundary_preserves_non_oauth_context(monkeypatch, auth):
    from langboard.mcp_integration import NativeConsent

    monkeypatch.setattr(NativeConsent, "_scope", lambda *_args: pytest.fail("Unexpected OAuth scope"))
    outer = {"outer": True}
    auth_token, scope_token = mcp_auth_context.set(auth), native_mcp_boundary.set(outer)
    try:
        with NativeConsent.native_owner_boundary("read_wiki_content", {}):
            assert native_mcp_boundary.get() is outer
        assert native_mcp_boundary.get() is outer
    finally:
        native_mcp_boundary.reset(scope_token)
        mcp_auth_context.reset(auth_token)
