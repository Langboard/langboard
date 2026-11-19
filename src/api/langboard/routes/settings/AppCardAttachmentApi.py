"""Upload and reconcile an owning app's native card attachment."""

from typing import Annotated
from fastapi import File, Form, Header, Path, UploadFile
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.app_card_attachments import (
    read_app_card_attachment_receipt,
    upload_app_card_attachment,
)
from langboard_shared.domain.services.AppExecutionGrant import AppExecutionCredentialDenied
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied


Uid = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")]
FileKey = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Authorization = Annotated[str, Header(max_length=256)]
ROUTE = "/apps/v1/boards/{project_uid}/cards/{card_uid}/attachments/{file_key}"


def _invoke(operation, authorization, project_uid, card_uid, file_key, *args):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    try:
        return operation(
            token, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(card_uid), file_key, *args
        )
    except AppExecutionCredentialDenied as exc:
        raise ApiException.Unauthorized_401() from exc
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    except RuntimeError as exc:
        raise ApiException.InternalServerError_500() from exc


@AppRouter.api.post(ROUTE, tags=["App.Cards"])
def upload_owned_card_attachment(
    project_uid: Uid,
    card_uid: Uid,
    file_key: FileKey,
    attachment: Annotated[UploadFile, File()],
    sha256: Annotated[str, Form(pattern=r"^[0-9a-f]{64}$")],
    byte_size: Annotated[int, Form(gt=0)],
    authorization: Authorization = "",
):
    result = _invoke(
        upload_app_card_attachment, authorization, project_uid, card_uid, file_key, sha256, byte_size, attachment
    )
    return JsonResponse(
        content=result, status_code=201 if result["created"] else 200, headers={"Cache-Control": "no-store"}
    )


@AppRouter.api.get(ROUTE, tags=["App.Cards"])
def read_owned_card_attachment_receipt(
    project_uid: Uid, card_uid: Uid, file_key: FileKey, authorization: Authorization = ""
):
    result = _invoke(read_app_card_attachment_receipt, authorization, project_uid, card_uid, file_key)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
