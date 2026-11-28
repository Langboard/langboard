"""Canonical card actions reuse native authority without nested MCP execution."""

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError, ToolError
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.ExecutionHistory import execution_scope
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardActionsMcp as actions
from langboard.mcp_tools import RoleChecker
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.models import Bot, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityInputRejected
from pydantic import TypeAdapter


class CardActionOwnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = User(
            id=1, firstname="Owner", lastname="QA", email="owner@example.invalid", password="fixture-only"
        )
        self.service = SimpleNamespace(close=Mock())
        self.changes = [
            {"action": "assign", "assign_user_uids": []},
            {"action": "label", "label_uids": []},
            {"action": "title", "title": "Title"},
            {"action": "priority", "priority": "P1"},
            {"action": "deadline", "deadline_at": ""},
            {"action": "next_action", "next_action": ""},
            {"action": "completion", "completed": False},
            {"action": "description_replace", "description": "Text", "expected_revision": "a" * 64},
            {"action": "visibility", "visibility": "INTERNAL", "expected_visibility": "SHARED"},
            {
                "action": "move",
                "column_uid": "column",
                "order": 0,
                "move_descendants": False,
                "confirmation_revision": "tree",
            },
            {"action": "archive"},
            {"action": "delete"},
            {"action": "attachment_update", "attachment_uid": "file", "order": 0},
            {"action": "attachment_delete", "attachment_uid": "file"},
            {
                "action": "attachment_upload",
                "filename": "a.txt",
                "file_data_base64": "YQ==",
                "request_uid": "12345678-1234-5678-1234-567812345678",
                "duplicate_action": "cancel",
            },
        ]

    def test_every_action_calls_one_owner_with_the_same_actor_and_service(self):
        for payload in self.changes:
            with self.subTest(action=payload["action"]):
                change = TypeAdapter(actions.CardChange).validate_python(payload)
                result = {"owner_result": payload["action"]}
                owner = Mock(return_value=result)
                actor_key = "user" if payload["action"] in {"visibility", "attachment_upload"} else "user_or_bot"
                metadata = {"handler": owner, "exclude": [actor_key, "service"], "accessible_type": "all"}
                with (
                    patch.object(McpTool, "get_tool", return_value=metadata),
                    patch.object(McpServer, "_wrap_tool", side_effect=AssertionError("Nested MCP boundary")),
                    patch.object(actions.McpRoleChecker, "check_permission", return_value=True) as acl,
                ):
                    self.assertIs(actions.update_card("board", "card", change, self.actor, self.service), result)
                owner.assert_called_once()
                self.assertIs(owner.call_args.kwargs[actor_key], self.actor)
                self.assertIs(owner.call_args.kwargs["service"], self.service)
                self.assertEqual(owner.call_args.kwargs["project_uid"], "board")
                self.assertEqual(owner.call_args.kwargs["card_uid"], "card")
                self.assertNotIn("action", owner.call_args.kwargs)
                self.assertEqual(acl.call_count, 1)
                if payload["action"] == "visibility":
                    self.assertNotIn("confirmed", owner.call_args.kwargs)
                if payload["action"] == "move":
                    self.assertIs(owner.call_args.kwargs["move_descendants"], False)
                    self.assertEqual(owner.call_args.kwargs["confirmation_revision"], "tree")
        self.service.close.assert_not_called()

    def test_delete_reuses_native_delete_role_and_denies_before_owner(self):
        owner = McpTool.get_tool("delete_card")["handler"]
        required = []

        def deny(user_id, arguments, permissions, finder):
            required.append(permissions)
            return False

        security = SimpleNamespace(is_authorized=deny)
        with patch.object(RoleChecker, "RoleSecurity", return_value=security), self.assertRaises(AuthorizationError):
            actions.update_card("board", "card", actions.DeleteCard(action="delete"), self.actor, self.service)
        self.assertEqual(required, [[ProjectRoleAction.CardDelete.value]])
        self.assertIs(McpTool.get_tool("delete_card")["handler"], owner)

    def test_user_only_native_owner_rejects_bot_before_role_or_write(self):
        bot = Bot(
            id=2,
            name="QA",
            bot_uname="qa",
            app_api_token="fixture",
            platform="default",
            platform_running_type="default",
        )
        owner = Mock()
        metadata = {"handler": owner, "exclude": ["user", "service"], "accessible_type": "user"}
        with (
            patch.object(McpTool, "get_tool", return_value=metadata),
            patch.object(actions.McpRoleChecker, "check_permission") as acl,
            self.assertRaisesRegex(AuthorizationError, "Authentication required"),
        ):
            actions.update_card(
                "board",
                "card",
                actions.ChangeCardVisibility(action="visibility", visibility="INTERNAL", expected_visibility="SHARED"),
                bot,
                self.service,
            )
        owner.assert_not_called()
        acl.assert_not_called()

    def test_native_error_identity_is_preserved(self):
        for error in (CardVisibilityInputRejected("preflight"), RuntimeError("after write")):
            owner = Mock(side_effect=error)
            metadata = {"handler": owner, "exclude": ["user", "service"], "accessible_type": "user"}
            with (
                patch.object(McpTool, "get_tool", return_value=metadata),
                patch.object(actions.McpRoleChecker, "check_permission", return_value=True),
                self.assertRaises(type(error)) as result,
            ):
                actions.update_card(
                    "board",
                    "card",
                    actions.ChangeCardVisibility(
                        action="visibility", visibility="INTERNAL", expected_visibility="SHARED"
                    ),
                    self.actor,
                    self.service,
                )
            self.assertIs(result.exception, error)

    def test_execution_history_keeps_the_native_move_scope(self):
        scope = {}
        token = execution_scope.set(scope)
        owner = Mock(return_value={"moved": True})
        metadata = {"handler": owner, "exclude": ["user_or_bot", "service"], "accessible_type": "all"}
        try:
            with (
                patch.object(McpTool, "get_tool", return_value=metadata),
                patch.object(actions.McpRoleChecker, "check_permission", return_value=True),
                patch.object(actions, "safe_scope", return_value={"project_column_id": 3}) as history,
            ):
                actions.update_card(
                    "board",
                    "card",
                    actions.MoveCard(action="move", column_uid="column", order=0),
                    self.actor,
                    self.service,
                )
            self.assertEqual(scope, {"project_column_id": 3})
            self.assertEqual(history.call_args.args[2]["column_uid"], "column")
            self.assertNotIn("service", history.call_args.args[2])
        finally:
            execution_scope.reset(token)

    async def test_native_dependency_lifecycle_is_owned_once_on_success_and_failure(self):
        change = {"action": "title", "title": "Title"}
        for fails in (False, True):
            created, closed = ([], [])

            def make_resource(created, closed):

                @asynccontextmanager
                async def owned_resource():
                    created.append(True)
                    try:
                        yield self.service
                    finally:
                        closed.append(True)

                return owned_resource

            resource = make_resource(created, closed)

            def factory(kind, resource=resource):
                return resource

            def owner(should_fail=fails, **kwargs):
                self.assertIs(kwargs["service"], self.service)
                self.assertIs(kwargs["user_or_bot"], self.actor)
                if should_fail:
                    raise RuntimeError("owner failed")
                return {"title": "Title"}

            metadata = {"handler": owner, "exclude": ["user_or_bot", "service"], "accessible_type": "all"}
            token = mcp_auth_context.set({"user_or_bot": self.actor})
            try:
                with (
                    patch.object(Dependencies, "_factory_dependency", factory),
                    patch.object(McpTool, "get_tool", return_value=metadata),
                    patch.object(actions.McpRoleChecker, "check_permission", return_value=True),
                    patch.object(McpServer, "_wrap_tool", side_effect=AssertionError("Nested MCP boundary")),
                ):
                    server = FastMCP("native-card-owner")
                    server.tool(name="update_card")(Dependencies.with_native_dependencies(actions.update_card))
                    async with Client(server) as client:
                        schema = (await client.list_tools())[0].input_schema
                        self.assertNotIn("service", schema["properties"])
                        self.assertNotIn("user_or_bot", schema["properties"])
                        if fails:
                            with self.assertRaises(ToolError):
                                await client.call_tool(
                                    "update_card", {"project_uid": "board", "card_uid": "card", "change": change}
                                )
                        else:
                            result = await client.call_tool(
                                "update_card", {"project_uid": "board", "card_uid": "card", "change": change}
                            )
                            self.assertEqual(result.structured_content, {"title": "Title"})
                self.assertEqual(created, [True])
                self.assertEqual(closed, [True])
            finally:
                mcp_auth_context.reset(token)
