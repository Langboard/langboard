"""Authenticated board-local Team organization; never a board invitation or grant."""

from typing import Annotated
from fastapi import Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services.BoardTeams import BoardTeams, TeamConflict, TeamDenied, TeamMissing
from langboard_shared.security import Auth
from .forms.Team import TeamCreate, TeamGroupImport, TeamMemberDelta, TeamRename, TeamRevision


def _result(operation):
    try:
        return JsonResponse(content=operation(), headers={"Cache-Control": "no-store"})
    except TeamDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except TeamMissing as exc:
        raise ApiException.NotFound_404() from exc
    except TeamConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.get("/board/{project_uid}/teams", tags=["Board.Team"])
@AuthFilter.add("user")
def list_teams(
    project_uid: str,
    query: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    user: User = Auth.scope("user"),
):
    return _result(lambda: BoardTeams().list(user.id, project_uid, query=query, page=page, limit=limit))


@AppRouter.api.get("/board/{project_uid}/teams/candidates", tags=["Board.Team"])
@AuthFilter.add("user")
def team_candidates(
    project_uid: str,
    query: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    user: User = Auth.scope("user"),
):
    return _result(lambda: BoardTeams().candidates(user.id, project_uid, query=query, page=page, limit=limit))


@AppRouter.api.get("/board/{project_uid}/teams/{team_uid}", tags=["Board.Team"])
@AuthFilter.add("user")
def get_team(project_uid: str, team_uid: str, user: User = Auth.scope("user")):
    return _result(lambda: BoardTeams().get(user.id, project_uid, team_uid))


@AppRouter.api.post("/board/{project_uid}/teams", tags=["Board.Team"])
@AuthFilter.add("user")
def create_team(project_uid: str, form: TeamCreate, user: User = Auth.scope("user")):
    return _result(lambda: BoardTeams().mutate(user.id, project_uid, "create", **form.model_dump()))


@AppRouter.api.patch("/board/{project_uid}/teams/{team_uid}", tags=["Board.Team"])
@AuthFilter.add("user")
def rename_team(project_uid: str, team_uid: str, form: TeamRename, user: User = Auth.scope("user")):
    return _result(lambda: BoardTeams().mutate(user.id, project_uid, "rename", team_uid=team_uid, **form.model_dump()))


@AppRouter.api.delete("/board/{project_uid}/teams/{team_uid}", tags=["Board.Team"])
@AuthFilter.add("user")
def delete_team(project_uid: str, team_uid: str, form: TeamRevision, user: User = Auth.scope("user")):
    return _result(lambda: BoardTeams().mutate(user.id, project_uid, "delete", team_uid=team_uid, **form.model_dump()))


@AppRouter.api.post("/board/{project_uid}/teams/{team_uid}/members", tags=["Board.Team"])
@AuthFilter.add("user")
def add_team_members(project_uid: str, team_uid: str, form: TeamMemberDelta, user: User = Auth.scope("user")):
    return _result(
        lambda: BoardTeams().mutate(user.id, project_uid, "add_members", team_uid=team_uid, **form.model_dump())
    )


@AppRouter.api.delete("/board/{project_uid}/teams/{team_uid}/members", tags=["Board.Team"])
@AuthFilter.add("user")
def remove_team_members(project_uid: str, team_uid: str, form: TeamMemberDelta, user: User = Auth.scope("user")):
    return _result(
        lambda: BoardTeams().mutate(user.id, project_uid, "remove_members", team_uid=team_uid, **form.model_dump())
    )


@AppRouter.api.get("/board/{project_uid}/teams/{team_uid}/group-preview", tags=["Board.Team"])
@AuthFilter.add("user")
def preview_team_group(
    project_uid: str,
    team_uid: str,
    group_uid: Annotated[str, Query(pattern=r"^[0-9A-Za-z]{11}$")],
    user: User = Auth.scope("user"),
):
    return _result(lambda: BoardTeams().group_preview(user.id, project_uid, team_uid, group_uid))


@AppRouter.api.post("/board/{project_uid}/teams/{team_uid}/group-add", tags=["Board.Team"])
@AuthFilter.add("user")
def import_team_group(project_uid: str, team_uid: str, form: TeamGroupImport, user: User = Auth.scope("user")):
    return _result(
        lambda: BoardTeams().mutate(user.id, project_uid, "group_add", team_uid=team_uid, **form.model_dump())
    )
