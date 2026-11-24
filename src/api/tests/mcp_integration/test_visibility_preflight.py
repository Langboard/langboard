"""Only proven visibility preflight rejection is a not-applied outcome."""

import importlib
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
from fastapi import FastAPI, Request
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from httpx import ASGITransport, AsyncClient
from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
from langboard.mcp_tools.CardManagementMcp import change_card_visibility
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import User
from langboard_shared.domain.services.CardVerification import VerificationInputRejected
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityInputRejected


class VisibilityPreflightTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = User(
            id=1, firstname="Receipt", lastname="QA", email="receipt@example.invalid", password="fixture-only"
        )
        self.owner = Mock(return_value={"visibility": "INTERNAL"})
        from langboard_shared.domain.services.factory.CardService import CardService

        card_owner = SimpleNamespace(change_visibility=self.owner)
        card_owner.review_visibility_change = MethodType(CardService.review_visibility_change, card_owner)
        self.service = SimpleNamespace(card=card_owner)
        self.arguments = {
            "project_uid": "board",
            "card_uid": "card",
            "visibility": "INTERNAL",
            "expected_visibility": "PRIVATE",
        }

    def test_confirmation_rejection_never_calls_mutation_owner(self):
        cases = [
            {},
            {"confirmed": "true"},
            {"confirmed": True},
            {"confirmed": True, "expected_updated_at": "invalid"},
            {"confirmed": True, "expected_updated_at": "2026-11-15T10:00:00"},
            {"confirmed": False, "expected_updated_at": "2026-11-15T10:00:00Z"},
        ]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(CardVisibilityInputRejected):
                change_card_visibility(**self.arguments, user=self.actor, service=self.service, **fields)
        self.owner.assert_not_called()

    def test_valid_confirmation_uses_native_owner_once(self):
        result = change_card_visibility(
            **self.arguments,
            user=self.actor,
            service=self.service,
            confirmed=True,
            expected_updated_at="2026-11-15T10:00:00Z",
        )
        self.assertEqual(result, {"visibility": "INTERNAL"})
        self.owner.assert_called_once()
        self.assertEqual(self.owner.call_args.kwargs["expected_updated_at"].isoformat(), "2026-11-15T10:00:00+00:00")

    def test_owner_failure_is_not_reclassified_as_preflight(self):
        self.owner.side_effect = ValueError("after-persistence-private-detail")
        with self.assertRaises(ToolError) as result:
            change_card_visibility(
                **self.arguments,
                user=self.actor,
                service=self.service,
                confirmed=True,
                expected_updated_at="2026-11-15T10:00:00Z",
            )
        self.assertNotIsInstance(result.exception, CardVisibilityInputRejected)
        self.owner.assert_called_once()

    async def test_modern_receipt_distinguishes_preflight_and_unknown_owner_failure(self):
        context = SimpleNamespace(message=SimpleNamespace(name="update_card"))
        for error, outcome in [
            (CardVisibilityInputRejected("private"), "not_applied"),
            (ToolError("private"), "unknown"),
        ]:

            async def fail(_, error=error):
                raise error

            result = await MutationReceiptMiddleware().on_call_tool(context, fail)
            self.assertEqual(result.meta["mutation_receipt"]["outcome"], outcome)
            self.assertFalse(result.meta["mutation_receipt"]["retryable"])
            self.assertNotIn("private", str(result.content))

    async def test_http_primitive_and_canonical_preserve_preflight_and_unknown_boundaries(self):
        route = importlib.import_module("langboard.routes.mcp.McpApi")
        for name in ("change_card_visibility", "update_card"):
            for after_write in (False, True):
                with self.subTest(name=name, after_write=after_write):
                    effects, closed = [], []
                    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)

                    def factory(group=group, closed=closed):
                        return SimpleNamespace(
                            mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group),
                            close=lambda: closed.append(True),
                        )

                    server = FastMCP("visibility-preflight-http")

                    def invoke_factory(after_write, effects):
                        def invoke() -> dict:
                            def owner(*args, **kwargs):
                                effects.append("saved")
                                raise ValueError("private-owner-detail")

                            from langboard_shared.domain.services.factory.CardService import CardService

                            card_owner = SimpleNamespace(change_visibility=owner)
                            card_owner.review_visibility_change = MethodType(
                                CardService.review_visibility_change, card_owner
                            )
                            return change_card_visibility(
                                **self.arguments,
                                user=self.actor,
                                service=SimpleNamespace(card=card_owner),
                                **(
                                    {"confirmed": True, "expected_updated_at": "2026-11-15T10:00:00Z"}
                                    if after_write
                                    else {}
                                ),
                            )

                        return invoke

                    server.tool(name=name)(invoke_factory(after_write, effects))

                    app = FastAPI()

                    @app.post("/invoke")
                    async def dispatch(request: Request, selected_name: str = name):
                        request.scope["auth"] = SimpleNamespace(id=1)
                        return await route.execute_mcp_tool(selected_name, request)

                    with (
                        patch.object(route, "DomainService", factory),
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
                    self.assertEqual(response.status_code, 500 if after_write else 400)
                    payload = response.json()
                    self.assertEqual(payload["outcome"], "unknown" if after_write else "not_applied")
                    self.assertEqual(effects, ["saved"] if after_write else [])
                    self.assertEqual(closed, [True])
                    self.assertNotIn("private", response.text)
                    if not after_write:
                        self.assertEqual(payload["code"], "VISIBILITY_INPUT_REJECTED")
                        self.assertFalse(payload["retryable"])
                        self.assertEqual(payload["next_action"], "read_and_review")
                        self.assertEqual(payload["request_id"], "a" * 32)
                    self.assertIsNone(route.mcp_auth_context.get())

    async def test_existing_verification_rejection_keeps_its_own_receipt(self):
        context = SimpleNamespace(message=SimpleNamespace(name="record_card_verification_evidence"))

        async def fail(_):
            raise VerificationInputRejected("private")

        result = await MutationReceiptMiddleware().on_call_tool(context, fail)
        self.assertEqual(result.meta["mutation_receipt"]["outcome"], "not_applied")
        self.assertIn("Verification input rejected", str(result.content))
