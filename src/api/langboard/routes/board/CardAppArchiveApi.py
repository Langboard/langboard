"""Human archive synchronization configuration and visible native receipts."""

from fastapi import Query, Request
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import AppCardArchive, AppExecutionOutbox, User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardArchive import configure_archive_sync
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class ArchiveSyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    card_types: list[str] = Field(max_length=64)


@AppRouter.api.put("/board/{project_uid}/apps/{app_key}/archive-sync", tags=["Board.Apps"])
@AuthFilter.add("user")
def update_archive_sync(project_uid: str, app_key: str, body: ArchiveSyncBody,
                        user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    try:
        result = configure_archive_sync(service, user, SnowflakeID.from_short_code(project_uid), app_key,
                                        body.expected_revision, body.card_types)
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppGovernanceConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/apps/archives", tags=["Board.Card"])
@AuthFilter.add("user")
def list_archive_receipts(request: Request, project_uid: str, card_uid: str,
                          limit: int = Query(default=10, ge=1, le=50),
                          user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    resolved = service.card.resolve_readable_card(project_uid, card_uid, user,
        request.scope.get("collaboration_channel", CollaborationChannel.Api))
    if resolved is None:
        raise ApiException.NotFound_404() from None
    project, card, _ = resolved
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(AppCardArchive).where(AppCardArchive.project_id == project.id,
                       AppCardArchive.card_id == card.id).order_by(AppCardArchive.id.desc()).limit(limit)).all()
        from langboard_shared.domain.services.AppEventDestination import APP_CARD_ARCHIVE_EVENT

        events = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.request_id.in_([row.id for row in rows]),
                         AppExecutionOutbox.event_type == APP_CARD_ARCHIVE_EVENT)).all() if rows else []
        deliveries = {event.request_id: {"event_uid": event.get_uid(), "state": event.state,
                      "attempt_count": event.attempt_count} for event in events}
    return JsonResponse(content={"items": [{"archive_uid": row.get_uid(), "card_revision": row.card_revision,
                         "app_key": row.app_key, "receipt": row.receipt, "delivery": deliveries.get(row.id)} for row in rows]},
                        headers={"Cache-Control": "no-store"})
