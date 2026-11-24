"""Bounded native card-context search; preserve MCP visibility and cursor semantics.

The application owner is callable without registering or dispatching an MCP tool.
It retains the MCP collaboration channel; other transports must establish their
own channel/consent contract before reusing this projection.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from pydantic import Field
from ..projections import pick, public_card_summary, public_column_context, public_workflow_stages


def _parse_time_bound(value: str | None) -> SafeDateTime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Date bounds must include a timezone")
    return SafeDateTime.fromtimestamp(parsed.timestamp(), timezone.utc)


def search_card_context(
    project_uid: str | None,
    query: str,
    service: DomainService,
    date_field: Literal["created_at", "updated_at"] = "updated_at",
    since: str | None = None,
    until: str | None = None,
    include_closed: bool = False,
    workflow_stages: list[str] | None = None,
    visibility: list[str] | None = None,
    created_by: list[str] | None = None,
    assigned_to: list[str] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Field(ge=1, le=25, strict=True)] = 20,
    *,
    user: User,
) -> dict:
    """Search bounded card context through the native project-scoped query."""

    normalized_query = query.strip()
    if not 1 <= len(normalized_query) <= 1000:
        raise ValueError("query must contain between 1 and 1000 characters")

    from ...domain import CardQueryCursorRejected, CardQueryFilters, ProjectCardCursor

    filters = CardQueryFilters.normalize(
        include_closed=include_closed,
        workflow_stages=workflow_stages,
        visibility=visibility,
        created_by=created_by,
        assigned_to=assigned_to,
    )
    if isinstance(limit, bool) or not 1 <= limit <= 25:
        raise ValueError("limit must be between 1 and 25")
    lower, upper = _parse_time_bound(since), _parse_time_bound(until)
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("since must be earlier than until")
    context = filters.binding(
        str(user.id),
        project_uid,
        query=normalized_query,
        date_field=date_field,
        since=lower.isoformat() if lower else None,
        until=upper.isoformat() if upper else None,
    )
    decoded = ProjectCardCursor.decode(cursor) if cursor else None
    if decoded and decoded.context != context:
        raise CardQueryCursorRejected("Card cursor context changed or legacy cursor is unbound; restart without cursor")
    page = service.card.search_context_by_project(
        project_uid,
        normalized_query,
        date_field=date_field,
        since=lower,
        until=upper,
        include_closed=include_closed,
        workflow_stages=workflow_stages,
        visibility=visibility,
        created_by=created_by,
        assigned_to=assigned_to,
        limit=limit,
        paginated=True,
        before_updated_at=SafeDateTime.fromisoformat(decoded.updated_at) if decoded else None,
        before_card=decoded.card_uid if decoded else None,
        include_work_state=True,
        user=user,
        channel=CollaborationChannel.Mcp,
    )
    cards = page["cards"]
    next_cursor = ProjectCardCursor(*page["next_fields"], context=context).encode() if page["next_fields"] else None
    # Keep document retrieval bounded independently of full stored transcriptions.
    remaining_document_chars = 4_000
    for card in cards:
        matches = []
        for match in card.get("document_matches", [])[:2]:
            if remaining_document_chars <= 0:
                break
            snippet = str(match.get("snippet", ""))[: min(500, remaining_document_chars)]
            remaining_document_chars -= len(snippet)
            matches.append(
                {
                    "attachment_uid": match.get("attachment_uid"),
                    "filename": str(match.get("filename", ""))[:200],
                    "snippet": snippet,
                    **pick(
                        match,
                        ("chunk_id", "generation", "content_hash", "page", "pages", "provenance_scope"),
                        max_field_chars=120,
                    ),
                }
            )
        if matches:
            card["document_matches"] = matches
        else:
            card.pop("document_matches", None)
    keys = {card["work_state"]["workflow_stage"] for card in cards if card.get("work_state", {}).get("workflow_stage")}
    stages = service.workflow_stage.get_api_by_keys(keys) if keys else {}
    return {
        # Extraction and this result page are bounded; absence is not proof that
        # no other readable document exists within the requested scope.
        "search_scope": {
            "project_uid": project_uid,
            "query": normalized_query,
            "include_closed": include_closed,
            "workflow_stages": workflow_stages,
            "visibility": visibility,
            "created_by": created_by,
            "assigned_to": assigned_to,
            "date_field": date_field,
            "since": lower.isoformat() if lower is not None else None,
            "until": upper.isoformat() if upper is not None else None,
            "method": "lexical",
            "sources": "readable_card_context_and_extracted_attachment_text",
            "exhaustive": False,
        },
        "cards": [
            {
                **public_card_summary(card, compact_workflow=True),
                **({"project_uid": card["project_uid"]} if "project_uid" in card else {}),
                **pick(card, ("description", "document_matches"), max_field_chars=500),
            }
            for card in cards
        ],
        "workflow_stages": public_workflow_stages(stages),
        "total_count": page["total_count"],
        "next_cursor": next_cursor,
        "limit": limit,
        "columns": {
            key: value
            for board in {card.get("project_uid", project_uid) for card in cards}
            for key, value in public_column_context(
                service, board, [card for card in cards if card.get("project_uid", project_uid) == board]
            ).items()
        },
    }
