# ruff: noqa: F811
"""Independent wheel consumes native credential-scoped resource pages."""

from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard_sdk import AppResources, HttpTransport, NativeApiError
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import AppResourceBinding, BoardAppBinding, Project
from test_app_connection_resources import scope


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
async def test_sdk_resource_paging_and_revocation_use_actual_native_http(board):
    connection, _, binding, rows, token = scope(board)
    app = FastAPI()
    app.include_router(AppRouter.api)
    calls = []
    with TestClient(app) as client:
        async def request(method, path, **kwargs):
            calls.append((method, path))
            return client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

        resources = AppResources(HttpTransport(SimpleNamespace(request=request)))
        first = await resources.list(board[2].get_uid(), limit=2)
        assert first["connection_uid"] == connection.get_uid()
        assert first["binding_revision"] == binding.edit_revision()
        assert len(first["items"]) == 2
        assert first["items"][0]["resource_path"] == []
        assert first["items"][0]["health"] == "unknown"
        assert len(calls) == 1
        second = await resources.list(board[2].get_uid(), limit=2, after_uid=first["next_cursor"])
        assert [item["resource_uid"] for item in second["items"]] == [rows[-1].get_uid()]
        assert second["next_cursor"] is None
        with DbSession.atomic() as db:
            binding.granted_capabilities = []
            db.update(binding)
        with pytest.raises(NativeApiError) as denied:
            await resources.list(board[2].get_uid())
        assert denied.value.status_code == 403
        assert len(calls) == 3  # No retries, refresh or automatic page crawling.


@pytest.mark.asyncio
@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
async def test_sdk_shared_connection_keeps_board_resources_and_revocation_independent(board):
    connection, _, binding, rows, token = scope(board)
    with DbSession.atomic() as db:
        other_project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
        other_project.owner_id = board[1].id
        db.update(other_project)
        binding.workflow_mapping = {"active": board[5][0].get_uid()}
        db.update(binding)
        other_binding = BoardAppBinding(
            project_id=other_project.id, app_key=connection.app_key, state="enabled",
            granted_capabilities=["resources.read"], workflow_mapping={"review": "other-board-column"},
        )
        db.insert(other_binding)
        other_resource = AppResourceBinding(
            board_binding_id=other_binding.id, connection_id=connection.id,
            resource_type="issue", external_resource_id=rows[0].external_resource_id,
            is_selected=True, access_state="granted", health="healthy",
        )
        db.insert(other_resource)
    app = FastAPI()
    app.include_router(AppRouter.api)
    with TestClient(app) as client:
        async def request(method, path, **kwargs):
            return client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

        resources = AppResources(HttpTransport(SimpleNamespace(request=request)))
        first = await resources.list(board[2].get_uid())
        other = await resources.list(other_project.get_uid())
        assert first["connection_uid"] == other["connection_uid"] == connection.get_uid()
        assert {item["resource_uid"] for item in first["items"]} == {row.get_uid() for row in rows}
        assert [item["resource_uid"] for item in other["items"]] == [other_resource.get_uid()]
        with DbSession.atomic() as db:
            rows[0].is_selected = False
            rows[0].access_state = "revoked"
            rows[0].health = "unavailable"
            db.update(rows[0])
        assert len((await resources.list(board[2].get_uid()))["items"]) == 2
        assert (await resources.list(other_project.get_uid())) == other
        with DbSession.atomic() as db:
            retained = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == other_binding.id)).first()
            assert retained.workflow_mapping == {"review": "other-board-column"}
            other_project.owner_id = 2
            db.update(other_project)
        with pytest.raises(NativeApiError) as denied:
            await resources.list(other_project.get_uid())
        assert denied.value.status_code == 403
        assert len((await resources.list(board[2].get_uid()))["items"]) == 2
