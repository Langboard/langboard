"""Native instance infrastructure settings. No credential material crosses this API."""

from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services import KeywordSearchSettings as settings
from langboard_shared.domain.services.factory.SecretReferenceService import (
    SecretReferenceConflict,
    SecretReferenceUnavailable,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from ...secrets.SecretInput import begin_input, input_status


class SettingsForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=0)
    endpoint: str = Field(default="", max_length=2048)
    index: str = Field(default="", max_length=128)
    secret_ref: str = Field(default="", max_length=128)
    secret_revision: int = Field(default=0, strict=True, ge=0)
    reads_enabled: bool = Field(default=False, strict=True)
    indexing_enabled: bool = Field(default=False, strict=True)


class RevisionForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=0)


class InputForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)


def _run(callback):
    try:
        response = JsonResponse(content=callback())
        response.headers["Cache-Control"] = "no-store"
        return response
    except (SecretReferenceConflict, IntegrityError):
        raise ApiException.Conflict_409() from None
    except SecretReferenceUnavailable:
        raise ApiException.Forbidden_403() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None


@AppRouter.api.get("/settings/keyword-search", tags=["AppSettings"])
@AuthFilter.add("admin")
def get_keyword_settings(cursor: str | None = None, user: User = Auth.scope("user")):
    return _run(lambda: settings.snapshot(user, cursor=cursor))


@AppRouter.api.put("/settings/keyword-search", tags=["AppSettings"])
@AuthFilter.add("admin")
def update_keyword_settings(
    form: SettingsForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return _run(lambda: settings.save(user, service, **form.model_dump()))


@AppRouter.api.post("/settings/keyword-search/connections/{connection_uid}/revoke", tags=["AppSettings"])
@AuthFilter.add("admin")
def revoke_keyword_connection(connection_uid: str, form: RevisionForm, user: User = Auth.scope("user")):
    return _run(lambda: settings.revoke_connection(user, connection_uid, form.expected_revision))


@AppRouter.api.post("/settings/keyword-search/secret-input", tags=["AppSettings"])
@AuthFilter.add("admin")
def create_keyword_secret_input(
    form: InputForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return _run(lambda: begin_input(service, user, "instance", "instance", form.name))


@AppRouter.api.get("/settings/keyword-search/secret-input/{input_uid}", tags=["AppSettings"])
@AuthFilter.add("admin")
def keyword_secret_input_status(
    input_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    def status():
        settings.require_admin(user)
        return input_status(service, user, input_uid)

    return _run(status)


@AppRouter.api.post("/settings/keyword-search/connections/{connection_uid}/preflight", tags=["AppSettings"])
@AuthFilter.add("admin")
def preflight_keyword_connection(connection_uid: str, user: User = Auth.scope("user")):
    from langboard_shared.core.search.ElasticsearchKeywordSearch import check_elasticsearch_connection
    from langboard_shared.helpers import InfraHelper

    def preflight():
        settings.require_admin(user)
        # Preflight uses the explicit retained grant and performs no document write.
        config = settings.configuration(operation="preflight", connection_id=InfraHelper.convert_id(connection_uid))
        options = settings.runtime_options(config, operation="preflight")
        return check_elasticsearch_connection(
            config["endpoint"],
            config["index"],
            options["api_key"],
            ca_certs=options["ca_certs"] or "",
            require_write=True,
        )

    return _run(preflight)
