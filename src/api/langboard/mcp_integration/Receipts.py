"""Mutation outcomes for modern transports, independent of legacy payloads."""

from typing import Literal
from uuid import uuid4
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from langboard_shared.core.exceptions.WikiContentConflict import WikiContentConflict
from langboard_shared.domain.contracts.content_blocks import ContentBlockConflictError
from langboard_shared.domain.services.CardVerification import VerificationConflict
from pydantic import BaseModel, ConfigDict
from ..card_workspace.domain import DescriptionPatchConflict, WorkPlanConflict
from .Annotations import READ_ONLY_TOOLS


class MutationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    tool: str
    outcome: Literal["applied", "not_applied", "unknown"]
    revision_conflict: bool = False
    retryable: bool = False
    next_action: Literal["none", "read_resource", "read_and_review"]


def is_revision_conflict(error: Exception) -> bool:
    """Only known pre-save conflict types prove that this attempt did not write."""
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (
                DescriptionPatchConflict,
                WorkPlanConflict,
                WikiContentConflict,
                ContentBlockConflictError,
                VerificationConflict,
            ),
        ):
            return True
        current = current.__cause__
    return False


class MutationReceiptMiddleware(Middleware):
    """Attach typed metadata without changing command data or authorization."""

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        if name == "call_raw_tool":
            name = (context.message.arguments or {}).get("name", name)
        if name in READ_ONLY_TOOLS:
            return await call_next(context)
        request_id = uuid4().hex
        try:
            result = await call_next(context)
        except AuthorizationError:
            raise
        except Exception as error:
            conflict = is_revision_conflict(error)
            receipt = MutationReceipt(
                request_id=request_id,
                tool=name,
                outcome="not_applied" if conflict else "unknown",
                revision_conflict=conflict,
                next_action="read_and_review" if conflict else "read_resource",
            )
            return ToolResult(
                content="Revision conflict; read and review a new request."
                if conflict
                else "Mutation outcome unknown; read the resource before retrying.",
                is_error=True,
                meta={"mutation_receipt": receipt.model_dump()},
            )
        if result.meta and "mutation_receipt" in result.meta:
            return result
        data = result.structured_content
        # Explicit disclosure previews await approval without applying the command.
        # Successful idempotent replays remain applied despite changed=False.
        human_action_required = (
            isinstance(data, dict)
            and data.get("changed") is False
            and (
                (name in {"change_card_visibility", "update_card"} and data.get("status") == "human_action_required")
                or (
                    name == "cross_board_card"
                    and data.get("operation") in {"clone", "move"}
                    and data.get("status") == "confirmation_required"
                )
            )
        )
        receipt = MutationReceipt(
            request_id=request_id,
            tool=name,
            outcome="unknown" if result.is_error else "not_applied" if human_action_required else "applied",
            next_action="read_resource" if result.is_error else "read_and_review" if human_action_required else "none",
        )
        return ToolResult(
            content=result.content,
            structured_content=result.structured_content,
            is_error=result.is_error,
            meta={**(result.meta or {}), "mutation_receipt": receipt.model_dump()},
        )
