"""Explicit credential custody; user responses contain metadata, never the bearer."""

from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppCredentialCustody import (
    CredentialHandoffUnknown, approve_credential_custody, handoff_connection_credential,
    renew_custodied_credential, revoke_credential_custody,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field
from .AppOperation import run_app_operation as _run


UID = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")]


@form_model
class CustodyApprovalForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_connection_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_destination_revision: int = Field(strict=True, ge=1)
    expected_revision: int | None = Field(default=None, strict=True, ge=1)


@form_model
class CustodyRevisionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(strict=True, ge=1)


@form_model
class CustodyHandoffForm(CustodyRevisionForm):
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")


class CustodyRenewalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")


async def _handoff(operation, *args, **kwargs):
    try:
        result = await operation(*args, **kwargs)
    except CredentialHandoffUnknown:
        raise ApiException.ServiceUnavailable_503() from None
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppGovernanceConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/settings/apps/connections/{connection_uid}/credential-custody", tags=["AppSettings.Credentials"])
@AuthFilter.add("user")
def approve_custody(connection_uid: UID, form: CustodyApprovalForm, user: User = Auth.scope("user")):
    return _run(approve_credential_custody, user, SnowflakeID.from_short_code(connection_uid),
        expected_connection_revision=form.expected_connection_revision,
        expected_destination_revision=form.expected_destination_revision, expected_revision=form.expected_revision)


@AppRouter.api.post("/settings/apps/credential-custody/{custody_uid}/handoff", tags=["AppSettings.Credentials"])
@AuthFilter.add("user")
async def handoff_custody(custody_uid: UID, form: CustodyHandoffForm, user: User = Auth.scope("user")):
    return await _handoff(handoff_connection_credential, user, SnowflakeID.from_short_code(custody_uid),
        expected_revision=form.expected_revision, request_id=form.request_id)


@AppRouter.api.post("/settings/apps/credential-custody/{custody_uid}/revoke", tags=["AppSettings.Credentials"])
@AuthFilter.add("user")
def revoke_custody(custody_uid: UID, form: CustodyRevisionForm, user: User = Auth.scope("user")):
    return _run(revoke_credential_custody, user, SnowflakeID.from_short_code(custody_uid), expected_revision=form.expected_revision)


@AppRouter.api.post("/apps/v1/credential-custody/renew", tags=["App.Credentials"])
async def renew_custody(body: CustodyRenewalBody, authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return await _handoff(renew_custodied_credential, token, request_id=body.request_id)
