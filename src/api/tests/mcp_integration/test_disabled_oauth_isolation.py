"""Disabled optional OAuth must not construct its provider or start discovery."""

import os
import subprocess
import sys
import textwrap


def test_disabled_transport_does_not_construct_optional_oauth_provider():
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from langboard.mcp_integration import Server
            from langboard_shared.Env import Env
            McpServer = Server.McpServer
            def reject_provider():
                raise AssertionError("Disabled OAuth provider was constructed")
            Server.create_oauth_provider = reject_provider
            type(Env).MCP_OAUTH_ENABLED = property(lambda _: False)
            McpServer.oauth_discovery_routes = ["stale fixture discovery"]
            assert McpServer.get_oauth_http_app() is None
            assert McpServer.oauth_discovery_routes == []
        """)],
        env={**os.environ, "PROJECT_NAME": "langboard", "MCP_OAUTH_ENABLED": "false"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
