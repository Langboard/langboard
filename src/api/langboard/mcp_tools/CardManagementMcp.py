"""Card management forwards existing native owners without granting app authority."""

from dataclasses import replace
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID
from fastmcp.exceptions import ToolError
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import Card, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardInteractions import list_card_interactions
from langboard_shared.domain.services.AppExecutionControl import list_human_card_runtimes, request_human_runtime_stop
from langboard_shared.domain.services.AppExecutionRequests import AppExecutionRequestConflict
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.Env import Env
from langboard_shared.security import RoleFinder
from pydantic import BaseModel, ConfigDict, Field
from ..apps.CardSignal import CardSignalConflict, bind_check, list_card_resources, read_checks, unlink_check
from ..apps.GitHubManifest import GitHubManifestUnavailable
from ..apps.SignalInbox import list_board_signals
from ..mcp_integration import McpRoleFilter, McpTool
from .CardAppResourcesMcp import Uid


@McpTool.add(
    "user",
    description="Change SHARED to INTERNAL using the current expected_visibility. SHARED disclosure only returns a read-only handoff to the existing human card-detail visibility confirmation UI; a person must review and confirm there. MCP cannot confirm or execute sharing. PRIVATE transitions are unavailable.",
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
def change_card_visibility(
    project_uid: Uid,
    card_uid: Uid,
    visibility: Literal["INTERNAL", "SHARED"],
    expected_visibility: Literal["INTERNAL", "SHARED"],
    user: User,
    service: DomainService,
) -> dict[str, Any]:
    try:
        if not isinstance(user, User):
            raise PermissionError()
        target = CardVisibility(visibility)
        if target == CardVisibility.Shared:
            resolved = service.card.resolve_readable_card(
                project_uid, card_uid, user, CollaborationChannel.Mcp, metadata_only=True
            )
            if resolved is None:
                raise PermissionError()
            project, card, context = resolved
            # Match the mutation owner's current primary-database role check.
            with DbSession.use(readonly=False) as db:
                actor = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)).first()
                role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.user_id == user.id, ProjectRole.project_id == project.id,
                )).first()
                roles = ["*"] if actor and actor.is_admin else (role.actions if role else [])
            context = replace(context, can_update_card="*" in roles or ProjectRoleAction.CardUpdate.value in roles)
            if (
                card.source_type == Card.LINKED_RESOURCE_PROJECT_CARD
                or card.visibility != expected_visibility
                or not context.can_change_visibility(CardVisibility(card.visibility), CardVisibility.Internal)
            ):
                raise PermissionError()
            if card.visibility == target.value:
                return {"status": "unchanged", "changed": False, "visibility": card.visibility}
            url = f"{Env.PUBLIC_UI_URL.rstrip('/')}/board/{quote(project_uid, safe='')}/{quote(card_uid, safe='')}"
            return {
                "status": "human_action_required",
                "changed": False,
                "visibility": visibility,
                "expected_visibility": expected_visibility,
                "card_url": url,
                "message": "A human must open the card detail, review the visibility disclosure, and confirm in the existing visibility UI. This MCP request does not change visibility.",
            }
        result = service.card.change_visibility(
            user, project_uid, card_uid, target,
            channel=CollaborationChannel.Mcp, confirmed=False, expected_visibility=expected_visibility,
        )
        if result is None:
            raise PermissionError()
        return result
    except (PermissionError, ValueError):
        raise ToolError("Card visibility transition unavailable; read current state and verify access") from None


class CrossBoardExclusionCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    members: int = Field(ge=0)
    labels: int = Field(ge=0)


class CrossBoardCardOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["link", "clone", "move"]
    status: Literal["confirmation_required", "applied", "unchanged"]
    changed: bool
    request_uid: str
    excluded: CrossBoardExclusionCounts
    new_reader_count: int = Field(default=0, ge=0)
    confirmation_token: str | None = None
    project_uid: Uid | None = None
    card_uid: Uid | None = None
    card_url: str | None = None
    card_link_markdown: str | None = None


@McpTool.add(
    "user",
    description="Only on explicit user instruction, link, independently clone, or move one card to another board column. Uses current source/target grants and excludes app-owned cards. Clone/move may return confirmation_required without mutation when new readers would gain access: show new_reader_count and exclusion counts, then obtain explicit user approval before sending confirmed=true with that exact current confirmation_token. Never confirm automatically or infer consent from a read. Keep the same request_uid UUID and identical operation/targets for retries after an unknown outcome; do not invent a new request. MOVE preserves card identity; LINK creates a read-only mirror without granting source access. No remote provider or app authority is granted.",
)
def cross_board_card(
    source_project_uid: Uid,
    source_card_uid: Uid,
    destination_project_uid: Uid,
    destination_column_uid: Uid,
    operation: Literal["link", "clone", "move"],
    request_uid: Annotated[
        str,
        Field(
            pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            json_schema_extra={"format": "uuid"},
        ),
    ],
    user: User,
    service: DomainService,
    confirmed: Annotated[bool, Field(strict=True)] = False,
    confirmation_token: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None,
) -> CrossBoardCardOutput:
    try:
        request_uid = str(UUID(request_uid))
    except (TypeError, ValueError, AttributeError):
        raise ToolError("A stable request UUID is required") from None
    if (
        type(confirmed) is not bool
        or confirmed != (confirmation_token is not None)
        or (operation == "link" and confirmed)
    ):
        raise ToolError("Explicit confirmation requires the current preview token")
    arguments = (user, source_project_uid, source_card_uid, destination_project_uid, destination_column_uid)
    try:
        if operation == "link":
            result = service.card.link_cross_board_card(*arguments, channel=CollaborationChannel.Mcp)
            if result is None:
                raise ToolError("Cross-board operation unavailable")
            _, card, changed = result
            excluded = {}
        elif operation in {"clone", "move"}:
            owner = service.card.clone_cross_board_card if operation == "clone" else service.card.move_cross_board_card
            result = owner(
                *arguments,
                request_uid=str(request_uid),
                confirmation_token=confirmation_token if confirmed else None,
                channel=CollaborationChannel.Mcp,
            )
            if result is None:
                raise ToolError("Cross-board operation unavailable")
            excluded = result.get("excluded", {})
            counts = CrossBoardExclusionCounts(
                members=len(excluded.get("member_uids", [])), labels=len(excluded.get("label_uids", []))
            )
            if result.get("confirmation_required"):
                return CrossBoardCardOutput(
                    operation=operation,
                    status="confirmation_required",
                    changed=False,
                    request_uid=str(request_uid),
                    excluded=counts,
                    confirmation_token=result["confirmation_token"],
                    new_reader_count=len(result.get("new_reader_uids", [])),
                )
            card, changed = result["card"], result["created" if operation == "clone" else "moved"]
        else:
            raise ToolError("Cross-board operation unavailable")
    except Exception:
        raise ToolError("Cross-board operation unavailable; verify current access and request identity") from None
    card_uid = card["uid"]
    url = f"{Env.PUBLIC_UI_URL.rstrip('/')}/board/{quote(destination_project_uid, safe='')}/{quote(card_uid, safe='')}"
    return CrossBoardCardOutput(
        operation=operation,
        status="applied" if changed else "unchanged",
        changed=changed,
        request_uid=str(request_uid),
        excluded=CrossBoardExclusionCounts(
            members=len(excluded.get("member_uids", [])), labels=len(excluded.get("label_uids", []))
        ),
        project_uid=destination_project_uid,
        card_uid=card_uid,
        card_url=url,
        card_link_markdown=f"[Open card in Langboard]({url})",
    )


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (AppGovernanceDenied, GitHubManifestUnavailable):
        raise ToolError("Card management unavailable") from None
    except (CardSignalConflict, AppExecutionRequestConflict):
        raise ToolError("Card management changed; read current state before another explicit request") from None


@McpTool.add(
    "user",
    description="Read bounded native signal evidence and binding revisions under current card access. External run reports are not native signal evidence.",
)
def get_card_signals(project_uid: Uid, card_uid: Uid, user: User, service: DomainService) -> dict[str, Any]:
    return _call(read_checks, service, user, project_uid, card_uid, channel=CollaborationChannel.Mcp)


@McpTool.add(
    "user",
    description="Discover currently consented native signal resources for a readable card; bounded page and opaque next_cursor. Does not grant resource consent or contact providers.",
)
def list_card_signal_resources(
    project_uid: Uid, card_uid: Uid, user: User, service: DomainService, cursor: Uid | None = None
) -> dict[str, Any]:
    return _call(list_card_resources, service, user, project_uid, card_uid, cursor, channel=CollaborationChannel.Mcp)


@McpTool.add(
    "user",
    description="Only on explicit user instruction, attach an existing native signal to this card. Read source_change_seq and binding revision first; null expected_revision creates a binding. Preserves native connection/resource consent. Never creates evidence from arbitrary external execution, grants approval, or changes workflow. On unknown outcome read back; never retry automatically.",
)
def attach_card_signal(
    project_uid: Uid,
    card_uid: Uid,
    connection_uid: Uid,
    resource_uid: Uid,
    signal_uid: Uid,
    source_change_seq: Annotated[int, Field(strict=True, ge=0)],
    expected_revision: Annotated[int | None, Field(strict=True, ge=0)],
    user: User,
    service: DomainService,
) -> dict[str, Any]:
    return _call(
        bind_check,
        service,
        user,
        project_uid,
        card_uid,
        connection_uid,
        resource_uid,
        signal_uid,
        source_change_seq,
        expected_revision,
        visibility_channel=CollaborationChannel.Mcp,
    )


@McpTool.add(
    "user",
    description="Only on explicit user instruction, unlink native signal evidence using its current binding revision. Retains history; does not change workflow or remote execution. On unknown outcome read back before another request.",
)
def unlink_card_signal(
    project_uid: Uid,
    card_uid: Uid,
    binding_uid: Uid,
    expected_revision: Annotated[int, Field(strict=True, ge=0)],
    user: User,
    service: DomainService,
) -> dict[str, Any]:
    return _call(
        unlink_check,
        service,
        user,
        project_uid,
        card_uid,
        binding_uid,
        expected_revision,
        channel=CollaborationChannel.Mcp,
    )


@McpTool.add(
    "user",
    description="Read retained app requests and response/delivery state under native card access; at most 25 items with opaque next_cursor. Does not answer, approve, or apply requested effects.",
)
def list_card_app_requests(
    project_uid: Uid,
    card_uid: Uid,
    user: User,
    service: DomainService,
    limit: Annotated[int, Field(strict=True, ge=1, le=25)] = 25,
    cursor: Uid | None = None,
) -> dict[str, Any]:
    return _call(
        list_card_interactions,
        service,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        limit=limit,
        before=SnowflakeID.from_short_code(cursor) if cursor else None,
        channel=CollaborationChannel.Mcp,
    )


@McpTool.add(
    "user",
    description="Read retained app execution authorization history under native card access; at most 10 items with opaque next_cursor. Authorization state is not proof of remote execution or termination. No credentials returned.",
)
def list_card_app_executions(
    project_uid: Uid,
    card_uid: Uid,
    user: User,
    service: DomainService,
    cursor: Uid | None = None,
) -> dict[str, Any]:
    return _call(
        list_human_card_runtimes,
        service,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        CollaborationChannel.Mcp,
        before=SnowflakeID.from_short_code(cursor) if cursor else None,
    )


@McpTool.add(
    "user",
    description="Only on explicit user request, request stop of an existing card app execution at its current generation. Revokes its existing permit; does not prove remote termination, issue credentials, grant approval, resume, or start execution. On unknown outcome read execution history before another request.",
)
def request_card_app_execution_stop(
    project_uid: Uid,
    card_uid: Uid,
    request_uid: Uid,
    generation: Annotated[int, Field(strict=True, ge=1)],
    user: User,
    service: DomainService,
) -> dict[str, Any]:
    return _call(
        request_human_runtime_stop,
        service,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
        generation,
        CollaborationChannel.Mcp,
    )


@McpTool.add(
    "user",
    description="Read bounded board-scope native signal candidates for a currently readable card. Current inbox owner enforces actor/connection/resource consent and omits signals already bound to readable cards; use get_card_signals for existing bindings. Returned signal_uid, connection_uid and resource_uid are native authorized IDs. At most 25 items with opaque next_cursor. No provider calls or evidence creation.",
)
def list_card_signal_candidates(
    project_uid: Uid,
    card_uid: Uid,
    user: User,
    service: DomainService,
    cursor: Uid | None = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=25)] = 25,
) -> dict[str, Any]:
    if service.card.resolve_readable_card(project_uid, card_uid, user, CollaborationChannel.Mcp) is None:
        raise ToolError("Card management unavailable")
    return _call(
        list_board_signals, service, user, project_uid, cursor, channel=CollaborationChannel.Mcp, page_size=limit
    )
