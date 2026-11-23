"""Canonical card actions reuse native authority without nested MCP execution."""

import unittest
from contextlib import ExitStack, asynccontextmanager
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
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Bot, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityInputRejected
from pydantic import TypeAdapter


class CardActionOwnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = User(
            id=1, firstname="Owner", lastname="QA", email="owner@example.invalid", password="fixture-only"
        )
        self.service = SimpleNamespace(close=Mock(), card=Mock(), card_attachment=Mock())
        self.project = object()
        self.card = SimpleNamespace(id=3, is_linked_resource=False)
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
                action = payload["action"]
                primitive = Mock(side_effect=AssertionError("Primitive business dispatch"))
                metadata = {"handler": primitive, "exclude": [], "accessible_type": "all"}
                fields = change.model_dump(exclude={"action"}, exclude_none=True)
                prefix = (self.actor, "board", "card")
                result = {"owner_result": action}
                expected = result
                kwargs = {}
                with ExitStack() as stack:
                    for name in ("_require_task_card", "_require_readable_card", "_get_card_in_project"):
                        lookup = stack.enter_context(
                            patch.object(actions, name, return_value=(self.project, self.card))
                        )
                        if name != "_get_card_in_project":
                            # These guards receive the caller's exact service lifetime.
                            lookup.side_effect = lambda *args: self.assertIs(args[-1], self.service) or (
                                self.project,
                                self.card,
                            )
                    if action in {"assign", "label"}:
                        owner = self.service.card.replace_people_and_labels
                        args = (self.actor, self.project, self.card)
                        kwargs = fields
                    elif action in {"title", "priority", "deadline", "next_action"}:
                        owner = self.service.card.update
                        args = (*prefix, actions.CardService.normalize_detail_fields(**fields))
                    elif action == "completion":
                        owner = self.service.card.set_card_completed
                        args, result, expected = (*prefix, False), True, {"completed": False}
                    elif action == "description_replace":
                        adapter = object()
                        adapter_factory = stack.enter_context(patch.object(actions, "_adapter", return_value=adapter))
                        owner = stack.enter_context(patch.object(actions, "replace_card_description"))
                        args = (adapter, "board", "card", "Text", "a" * 64)
                    elif action == "visibility":
                        owner = self.service.card.review_visibility_change
                        args = (*prefix, "INTERNAL", "SHARED", False, None)
                    elif action == "move":
                        owner = self.service.card.change_order
                        args = (*prefix, 0, "column")
                        kwargs = {
                            "move_descendants": False,
                            "confirmation_revision": "tree",
                            "channel": CollaborationChannel.Mcp,
                        }
                        result, expected = True, {"message": "Order changed or card moved"}
                    elif action == "archive":
                        owner = self.service.card.archive
                        args = (self.actor, self.project, self.card)
                        result, expected = True, {"message": "Archived"}
                    elif action == "delete":
                        owner = self.service.card.delete
                        args = prefix
                        result, expected = True, {"message": "Deleted"}
                    elif action == "attachment_upload":
                        owner = self.service.card_attachment.upload
                        args = prefix
                        result = expected = {"cancelled": True, "request_uid": str(change.request_uid)}
                    else:
                        attachment = SimpleNamespace(card_id=self.card.id)
                        self.service.card_attachment.get_by_id_like.return_value = attachment
                        args = (self.actor, self.project, self.card, attachment)
                        if action == "attachment_delete":
                            owner = self.service.card_attachment.delete
                            result, expected = True, {"deleted": True}
                        else:
                            owner = self.service.card_attachment.change_order
                            args = (self.project, self.card, attachment, 0)
                            kwargs = {"user": self.actor}
                            self.service.card_attachment.get_api_list_by_card.return_value = []
                            result = True
                    owner.return_value = result
                    stack.enter_context(patch.object(McpTool, "get_tool", return_value=metadata))
                    stack.enter_context(
                        patch.object(McpServer, "_wrap_tool", side_effect=AssertionError("Nested MCP boundary"))
                    )
                    acl = stack.enter_context(
                        patch.object(actions.McpRoleChecker, "check_permission", return_value=True)
                    )
                    actual = actions.update_card("board", "card", change, self.actor, self.service)
                    if action in {
                        "assign",
                        "label",
                        "title",
                        "priority",
                        "deadline",
                        "next_action",
                        "description_replace",
                        "visibility",
                    }:
                        self.assertIs(actual, result)
                    if action == "attachment_update":
                        self.assertEqual(actual["attachments"].items, [])
                    else:
                        self.assertEqual(actual, expected)
                    if action == "attachment_upload":
                        owner.assert_called_once()
                        self.assertEqual(owner.call_args.args[:3], args)
                        self.assertEqual(owner.call_args.args[3].filename, "a.txt")
                        self.assertEqual(
                            owner.call_args.kwargs,
                            {
                                "channel": CollaborationChannel.Mcp,
                                "request_uid": str(change.request_uid),
                                "duplicate_action": "cancel",
                                "confirmation_token": None,
                                "replacement_uid": None,
                            },
                        )
                    else:
                        owner.assert_called_once_with(*args, **kwargs)
                    if action == "description_replace":
                        self.assertIs(adapter_factory.call_args.args[0], self.actor)
                        self.assertIs(adapter_factory.call_args.args[1], self.service)
                        adapter_factory.assert_called_once_with(self.actor, self.service)
                    elif action == "attachment_update":
                        self.assertIs(owner.call_args.kwargs["user"], self.actor)
                    else:
                        self.assertIs(owner.call_args.args[0], self.actor)
                    primitive.assert_not_called()
                    acl.assert_called_once_with(
                        primitive,
                        self.actor,
                        {
                            "project_uid": "board",
                            "card_uid": "card",
                            **{
                                key: value
                                for key, value in fields.items()
                                if action != "visibility" or key != "confirmed"
                            },
                        },
                    )
                self.service.card.reset_mock()
                self.service.card_attachment.reset_mock()
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
        self.service.card.delete.assert_not_called()
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
        self.service.card.review_visibility_change.assert_not_called()

    def test_native_error_identity_is_preserved(self):
        for error in (CardVisibilityInputRejected("preflight"), RuntimeError("after write")):
            owner = self.service.card.review_visibility_change
            owner.side_effect = error
            primitive = Mock(side_effect=AssertionError("Primitive business dispatch"))
            metadata = {"handler": primitive, "exclude": ["user", "service"], "accessible_type": "user"}
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
            primitive.assert_not_called()
            owner.assert_called_once_with(self.actor, "board", "card", "INTERNAL", "SHARED", False, None)
            owner.reset_mock()

    def test_execution_history_keeps_the_native_move_scope(self):
        scope = {}
        token = execution_scope.set(scope)
        owner = Mock(side_effect=AssertionError("Primitive business dispatch"))
        self.service.card.change_order.return_value = True
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
            owner.assert_not_called()
            self.service.card.change_order.assert_called_once_with(
                self.actor,
                "board",
                "card",
                0,
                "column",
                move_descendants=None,
                confirmation_revision=None,
                channel=CollaborationChannel.Mcp,
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

            def native_update(actor, project_uid, card_uid, fields, should_fail=fails):
                self.assertIs(actor, self.actor)
                self.assertEqual((project_uid, card_uid, fields), ("board", "card", {"title": "Title"}))
                if should_fail:
                    raise RuntimeError("owner failed")
                return {"title": "Title"}

            owner = Mock(side_effect=AssertionError("Primitive business dispatch"))
            self.service.card.update.side_effect = native_update
            metadata = {"handler": owner, "exclude": ["user_or_bot", "service"], "accessible_type": "all"}
            token = mcp_auth_context.set({"user_or_bot": self.actor})
            try:
                with (
                    patch.object(Dependencies, "_factory_dependency", factory),
                    patch.object(actions, "_require_task_card", return_value=(self.project, self.card)) as lookup,
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
                owner.assert_not_called()
                lookup.assert_called_once_with("board", "card", self.actor, self.service)
                self.assertEqual(created, [True])
                self.assertEqual(closed, [True])
            finally:
                mcp_auth_context.reset(token)
