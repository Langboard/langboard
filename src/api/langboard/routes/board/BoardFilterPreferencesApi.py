"""Filter favorites belong to the signed-in account, not the board editor."""

from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.BoardFilterPreferences import BoardFilterPin, board_filter_preferences
from langboard_shared.domain.services.UserAppPreferences import PreferenceConflict
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from pydantic import Field, field_validator


@form_model
class BoardFilterPreferencesForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)
    pinned_filters: list[BoardFilterPin] = Field(max_length=32)

    @field_validator("pinned_filters")
    @classmethod
    def unique_conditions(cls, pins):
        if len({(pin.key, pin.value) for pin in pins}) != len(pins):
            raise ValueError("Filter shortcuts must have unique conditions")
        return pins


def _preferences(user, project_uid, **kwargs):
    try:
        return JsonResponse(
            content=board_filter_preferences(user, SnowflakeID.from_short_code(project_uid), **kwargs),
            headers={"Cache-Control": "no-store"},
        )
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except PreferenceConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX1003) from exc


@AppRouter.api.get("/board/{project_uid}/filter-preferences", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_filter_preferences(
    project_uid: str = Path(pattern=r"^[a-zA-Z0-9]{11}$"),
    user: User = Auth.scope("user"),
):
    return _preferences(user, project_uid)


@AppRouter.api.put("/board/{project_uid}/filter-preferences", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def save_filter_preferences(
    form: BoardFilterPreferencesForm,
    project_uid: str = Path(pattern=r"^[a-zA-Z0-9]{11}$"),
    user: User = Auth.scope("user"),
):
    return _preferences(
        user,
        project_uid,
        pinned_filters=[pin.model_dump() for pin in form.pinned_filters],
        expected_revision=form.expected_revision,
    )
