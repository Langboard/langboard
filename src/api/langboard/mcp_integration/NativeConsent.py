"""Install one native OAuth consent boundary before any tool or raw dispatch."""

from contextlib import asynccontextmanager
from fastmcp.exceptions import AuthorizationError
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
from langboard_shared.domain.services.NativeMcpBoundary import native_mcp_boundary
from langboard_shared.domain.services.NativeMcpConsent import effective_board_ids, positively_internal
from ..middlewares.McpAuthMiddleware import mcp_auth_context
from .Annotations import READ_ONLY_TOOLS


# These surfaces carry historical polymorphic JSON or instance authority rather
# than an attributable current board FK. Keep them explicitly unavailable until
# their own contracts can prove board isolation. All board/card/wiki commands
# continue through the same shared SQL boundary, including raw commands.
EXTERNAL_UNSUPPORTED_TOOLS = frozenset(
    {
        "get_unread_notifications",
        "mark_notification_read",
        "mark_notifications_read",
        "mark_all_notifications_read",
        "get_shared_user_activities",
        "get_current_user_activities",
        "get_project_activities",
        "get_project_column_activities",
        "get_card_activities",
        "get_wiki_activities",
        "list_employees",
        "get_employee_identity_proof",
        "create_project",
        "create_project_board",
        "provision_project",
        "get_bot_schedules_by_project",
        "get_bot_schedules_by_card",
        "get_bot_schedules_by_column",
        "schedule_bot_cron",
        "reschedule_bot_cron",
        "unschedule_bot_cron",
        "get_bot_hook",
        "upsert_bot_hook",
        "update_bot_hook",
        "delete_bot_hook",
        "get_project_bot_scopes",
        "get_card_bot_scopes",
        "get_column_bot_scopes",
        "get_secret_reference_metadata",
        "request_secret_input",
        "get_secret_input_status",
        "request_secret_rotation_input",
        "list_secret_reference_history",
        "convert_wiki_selection_to_secret",
    }
)


# Frozen reviewed registry: newly added capabilities fail closed until audited.
EXTERNAL_SUPPORTED_TOOLS = frozenset(
    {
        "add_card_comment",
        "add_project_people",
        "append_wiki_content",
        "apply_card_graph_patch",
        "apply_card_work_plan",
        "archive_card",
        "assign_card_to_me",
        "attach_card_signal",
        "cardify_card_checkitem",
        "change_card_checkitem_work",
        "change_card_checklist",
        "change_card_comment",
        "change_card_details",
        "change_card_label",
        "change_card_order_or_move_column",
        "change_card_visibility",
        "change_column_name",
        "change_column_order",
        "change_column_workflow_stage",
        "create_card",
        "create_card_checkitem",
        "create_card_checklist",
        "create_card_content_block",
        "create_card_in_leftmost_column",
        "create_column",
        "create_local_project_label",
        "create_project_wiki",
        "create_wiki_from_card",
        "cross_board_card",
        "delete_card",
        "delete_card_attachment",
        "delete_card_checkitem",
        "delete_card_checklist",
        "delete_card_comment",
        "delete_card_content_block",
        "delete_card_metadata",
        "delete_column",
        "delete_project_wiki",
        "delete_public_card_metadata",
        "delete_wiki_metadata",
        "diagnose_connection",
        "get_card",
        "get_card_app_mcp_tools",
        "get_card_app_resources",
        "get_card_attachments",
        "get_card_bundle",
        "get_card_delta",
        "get_card_execution_receipts",
        "get_card_linked_wikis",
        "get_card_metadata",
        "get_card_metadata_by_key",
        "get_card_signals",
        "get_card_timeline",
        "get_column_bot_schedules",
        "get_connection_context",
        "get_employee_status",
        "get_global_relationships",
        "get_my_work_cards",
        "get_project",
        "get_project_assigned_users",
        "get_project_checklists",
        "get_project_column_bot_scopes",
        "get_project_columns",
        "get_project_identity",
        "get_project_label_catalog",
        "get_project_labels",
        "get_project_workflow_stages",
        "get_projects",
        "get_public_card_metadata",
        "get_public_card_metadata_by_key",
        "get_starred_projects",
        "get_wiki_metadata",
        "get_wiki_metadata_by_key",
        "invite_project_members",
        "is_project_assignee",
        "is_project_available",
        "list_card_app_executions",
        "list_card_app_requests",
        "list_card_signal_candidates",
        "list_card_signal_resources",
        "list_my_work",
        "list_project_cards",
        "list_project_members",
        "list_project_wikis",
        "list_wiki_revisions",
        "move_card_content_block",
        "patch_card_description",
        "patch_wiki_content",
        "preview_card_work_plan",
        "read_card_attachment",
        "read_card_document",
        "read_card_images",
        "read_project_document",
        "read_wiki",
        "read_wiki_content",
        "read_wiki_revision",
        "reconcile_card_checklist_projection",
        "record_card_verification_evidence",
        "replace_card_description",
        "replace_wiki_content",
        "request_card_app_execution_stop",
        "request_project_document_processing",
        "save_card_metadata",
        "save_public_card_metadata",
        "save_wiki_metadata",
        "search_card_compaction_metadata",
        "search_card_document",
        "search_project_cards",
        "search_project_document",
        "search_project_people",
        "set_card_app_resource_selection",
        "set_card_completed",
        "set_card_people_and_labels",
        "set_card_relationships",
        "submit_card_execution_review",
        "toggle_card_comment_reaction",
        "toggle_star_project",
        "unassign_project_member",
        "unlink_card_signal",
        "update_card",
        "update_card_attachment",
        "update_card_checkitem",
        "update_card_checklist",
        "update_card_comment",
        "update_card_content_block",
        "update_card_linked_wiki",
        "update_project_members",
        "update_wiki",
        "upload_card_attachment",
        "use_global_project_label",
    }
)


def _scope(auth, tool_name, arguments):
    actor = auth["user_or_bot"]
    if positively_internal(actor):
        return None
    connection_id = auth.get("native_connection_id")
    if connection_id is None:
        raise AuthorizationError("Current native OAuth client connection required")
    if tool_name not in EXTERNAL_SUPPORTED_TOOLS:
        raise AuthorizationError("This capability cannot currently preserve external native board consent")
    reads = effective_board_ids(actor, connection_id, "read")
    writes = effective_board_ids(actor, connection_id, "write")
    is_read = tool_name in READ_ONLY_TOOLS
    allowed = reads if is_read else writes
    if not is_read and not writes:
        raise AuthorizationError("Explicit current board write consent required")
    project_uid = arguments.get("project_uid")
    if project_uid:
        from langboard_shared.core.types import SnowflakeID

        if int(SnowflakeID.from_short_code(project_uid)) not in allowed:
            raise AuthorizationError("Explicit current board consent required")
    return {
        "actor_id": int(actor.id),
        "connection_id": int(connection_id),
        "read": reads,
        "write": frozenset() if is_read else writes,
    }


@asynccontextmanager
async def native_call_boundary(tool_name, arguments):
    auth = mcp_auth_context.get()
    if not auth or auth.get("transport") != "oauth":
        yield
        return
    scope = await call_sync_fn_in_threadpool(_scope, auth, tool_name, arguments)
    token = native_mcp_boundary.set(scope)
    try:
        yield
    finally:
        native_mcp_boundary.reset(token)
