"""Bounded signed external observations under current app connection authority."""

from fastapi import Header, Request
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppSignalIngestion import AppSignalConflict, receive_external_signal
from starlette.concurrency import run_in_threadpool


@AppRouter.api.post("/apps/v1/boards/{project_uid}/signals", tags=["App.Signals"])
async def publish_signal(request: Request, project_uid: str, authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    for name in ("authorization", "x-langboard-webhook-version", "x-langboard-webhook-timestamp", "x-langboard-webhook-signature"):
        if len(request.headers.getlist(name)) != 1:
            raise ApiException.BadRequest_400()
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise ApiException.BadRequest_400()
        body.extend(chunk)
    return await run_in_threadpool(_receive_signal, token, project_uid, bytes(body), request.headers)


def _receive_signal(token, project_uid, body, headers):
    service = DomainService()
    try:
        result = receive_external_signal(service, token, project_uid, body, headers)
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppSignalConflict as exc:
        raise ApiException.Conflict_409() from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ApiException.BadRequest_400() from exc
    finally:
        service.close()
