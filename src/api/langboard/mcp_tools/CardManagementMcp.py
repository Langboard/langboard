"""Card management forwards existing native owners without granting app authority."""

from typing import Annotated, Any
from fastmcp.exceptions import ToolError
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardInteractions import list_card_interactions
from langboard_shared.domain.services.AppExecutionControl import list_human_card_runtimes, request_human_runtime_stop
from langboard_shared.domain.services.AppExecutionRequests import AppExecutionRequestConflict
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from pydantic import Field
from ..apps.CardSignal import CardSignalConflict, bind_check, list_card_resources, read_checks, unlink_check
from ..apps.GitHubManifest import GitHubManifestUnavailable
from ..apps.SignalInbox import list_board_signals
from ..mcp_integration import McpTool
from .CardAppResourcesMcp import Uid


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
