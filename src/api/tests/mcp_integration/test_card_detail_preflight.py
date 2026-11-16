"""Card detail input rejection must never imply a post-save rollback."""

import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, Request
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from httpx import ASGITransport, AsyncClient
from langboard.card_workspace.domain import CardInputRejected
from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
from langboard.mcp_tools import CardMcp
from langboard_shared.core.security import AuthSecurity


class CardDetailPreflightTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = Mock(return_value=True)
        self.service = SimpleNamespace(card=SimpleNamespace(update=self.owner))
        self.guard = patch.object(CardMcp, "_require_task_card", return_value=None).start()
        self.addCleanup(patch.stopall)

    def invoke(self, **fields):
        return CardMcp.change_card_details("board", "card", object(), self.service, **fields)

    def test_invalid_input_never_calls_authority_or_mutation_owner(self):
        for fields in ({}, {"title": " "}, {"deadline_at": "not-a-date"}, {"priority": "P9"}, {"next_action": "\x00"}):
            with self.subTest(fields=fields), self.assertRaises(CardInputRejected):
                self.invoke(**fields)
        self.guard.assert_not_called()
        self.owner.assert_not_called()

    def test_valid_deadline_and_explicit_clear_preserve_response_contract(self):
        for value in ("", "2026-11-16T12:00:00+09:00", "2026-11-16T12:00:00"):
            with self.subTest(value=value):
                self.owner.reset_mock()
                self.assertEqual(self.invoke(deadline_at=value), {"deadline_at": value})
                self.owner.assert_called_once()
                parsed = self.owner.call_args.args[-1]["deadline_at"]
                self.assertEqual(parsed is None, value == "")
                if parsed is not None:
                    self.assertIsNotNone(parsed.tzinfo)

    def test_owner_failure_is_not_reclassified(self):
        for error in (ValueError("private-owner-detail"), ToolError("private-owner-detail")):
            self.owner.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)) as caught:
                self.invoke(deadline_at="")
            self.assertNotIsInstance(caught.exception, CardInputRejected)

    async def test_modern_dispatch_preserves_not_applied_and_unknown(self):
        for after_write in (False, True):
            effects = []
            server = FastMCP("card-detail-preflight", middleware=[MutationReceiptMiddleware()])

            @server.tool(name="change_card_details")
            def invoke() -> dict:
                def owner(*args):
                    effects.append("saved")
                    raise ValueError("private-owner-detail")

                self.service.card.update = owner
                return self.invoke(deadline_at="" if after_write else "not-a-date")

            async with Client(server) as client:
                result = await client.call_tool("change_card_details", {}, raise_on_error=False)
            with self.subTest(after_write=after_write):
                self.assertTrue(result.is_error)
                receipt = result.meta["mutation_receipt"]
                self.assertEqual(receipt["outcome"], "unknown" if after_write else "not_applied")
                self.assertFalse(receipt["retryable"])
                self.assertEqual(effects, ["saved"] if after_write else [])
                self.assertNotIn("private-owner-detail", str(result.content))

    async def test_http_primitive_and_canonical_keep_the_same_boundary(self):
        route = importlib.import_module("langboard.routes.mcp.McpApi")
        for name in ("change_card_details", "update_card"):
            for after_write in (False, True):
                effects, closed = [], []
                group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
                service = SimpleNamespace(
                    mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group),
                    close=lambda: closed.append(True),
                )
                server = FastMCP("card-detail-http")

                def invoke() -> dict:
                    def owner(*args):
                        effects.append("saved")
                        raise ValueError("private-owner-detail")

                    self.service.card.update = owner
                    return self.invoke(deadline_at="" if after_write else "not-a-date")

                server.tool(name=name)(invoke)
                app = FastAPI()

                @app.post("/invoke")
                async def dispatch(request: Request, selected_name: str = name):
                    request.scope["auth"] = SimpleNamespace(id=1)
                    return await route.execute_mcp_tool(selected_name, request)

                with (
                    patch.object(route, "DomainService", return_value=service),
                    patch.object(route, "User", SimpleNamespace),
                    patch.object(route.McpTool, "get_tool", return_value={"accessible_type": "user"}),
                    patch.object(route.McpServer, "mcp", server),
                ):
                    async with AsyncClient(
                        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
                    ) as client:
                        response = await client.post(
                            "/invoke",
                            json={},
                            headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group", "X-Request-ID": "a" * 32},
                        )
                with self.subTest(name=name, after_write=after_write):
                    self.assertEqual(response.status_code, 500 if after_write else 400, response.text)
                    self.assertEqual(response.json()["outcome"], "unknown" if after_write else "not_applied")
                    if not after_write:
                        self.assertEqual(response.json()["code"], "CARD_INPUT_REJECTED")
                        self.assertEqual(response.json()["request_id"], "a" * 32)
                    self.assertEqual(effects, ["saved"] if after_write else [])
                    self.assertEqual(closed, [True])
                    self.assertNotIn("private-owner-detail", response.text)


if __name__ == "__main__":
    unittest.main()
