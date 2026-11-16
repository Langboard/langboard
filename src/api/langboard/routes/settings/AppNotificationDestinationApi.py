"""App-attested personal destinations require the initiating user's approval."""

from typing import Annotated, Literal
from fastapi import Header, Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppNotificationDestinations import (
    approve_destination_challenge, cancel_destination_challenge,
    create_destination_challenge, read_destination_challenge,
    read_notification_destination, revoke_notification_destination, submit_destination_proof,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field
from .AppOperation import run_app_operation as _run


UID = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")]


@form_model
class DestinationChallengeForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    expected_connection_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["personal"]


@form_model
class DestinationApprovalForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(strict=True, ge=1)
    proof_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")


@form_model
class DestinationRevisionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(strict=True, ge=1)


class ExternalIdentityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: str = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=255)


class PersonalDestinationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["personal"]
    external_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)


class DestinationProofBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    challenge_token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    external_identity: ExternalIdentityBody
    destination: PersonalDestinationBody


@AppRouter.api.post("/settings/apps/connections/{connection_uid}/notification-destination-challenges", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def create_challenge(connection_uid: UID, form: DestinationChallengeForm, user: User = Auth.scope("user")):
    return _run(
        create_destination_challenge, user, SnowflakeID.from_short_code(connection_uid),
        request_id=form.request_id, expected_connection_revision=form.expected_connection_revision,
    )


@AppRouter.api.get("/settings/apps/notification-destination-challenges/{challenge_uid}", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def read_challenge(challenge_uid: UID, user: User = Auth.scope("user")):
    return _run(read_destination_challenge, user, SnowflakeID.from_short_code(challenge_uid))


@AppRouter.api.post("/settings/apps/notification-destination-challenges/{challenge_uid}/approve", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def approve_challenge(challenge_uid: UID, form: DestinationApprovalForm, user: User = Auth.scope("user")):
    return _run(
        approve_destination_challenge, user, SnowflakeID.from_short_code(challenge_uid),
        expected_revision=form.expected_revision, proof_id=SnowflakeID.from_short_code(form.proof_uid),
    )


@AppRouter.api.post("/settings/apps/notification-destination-challenges/{challenge_uid}/cancel", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def cancel_challenge(challenge_uid: UID, form: DestinationRevisionForm, user: User = Auth.scope("user")):
    return _run(cancel_destination_challenge, user, SnowflakeID.from_short_code(challenge_uid), expected_revision=form.expected_revision)


@AppRouter.api.get("/settings/apps/notification-destinations/{destination_uid}", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def read_destination(destination_uid: UID, user: User = Auth.scope("user")):
    return _run(read_notification_destination, user, SnowflakeID.from_short_code(destination_uid))


@AppRouter.api.post("/settings/apps/notification-destinations/{destination_uid}/revoke", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def revoke_destination(destination_uid: UID, form: DestinationRevisionForm, user: User = Auth.scope("user")):
    return _run(revoke_notification_destination, user, SnowflakeID.from_short_code(destination_uid), expected_revision=form.expected_revision)


@AppRouter.api.post("/apps/v1/notification-destination-proofs", tags=["App.Notifications"])
def submit_proof(body: DestinationProofBody, authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        submit_destination_proof, token, SnowflakeID.from_short_code(body.challenge_uid),
        challenge_token=body.challenge_token, request_id=body.request_id,
        external_identity=body.external_identity.model_dump(), destination=body.destination.model_dump(),
    )
