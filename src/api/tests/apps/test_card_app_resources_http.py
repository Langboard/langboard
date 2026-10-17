# ruff: noqa: F811
"""Native human resource configuration preserves CAS, cleanup and visibility boundaries."""

import asyncio
from types import SimpleNamespace
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.board import CardAppResourcesApi  # noqa: F401
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import EmployeeMembershipPolicy
from langboard_shared.Env import Env
from test_card_app_resources import prepare


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_native_resource_selection_cas_cleanup_and_denial(board, monkeypatch):
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
    connection, _, binding, resources, card = prepare(board)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    path = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/connections/{connection.get_uid()}/resources"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        access, refresh = AuthSecurity.authenticate(board[1].id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        empty = client.get(path, headers=headers)
        assert empty.status_code == 200, empty.text
        assert empty.headers["cache-control"] == "no-store"
        assert empty.json()["revision"] is None and empty.json()["resource_uids"] == []
        form = {"resource_uids": [resources[0].get_uid()], "expected_revision": None}
        saved = client.put(path, headers=headers, json=form)
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1 and saved.json()["changed"] is True
        assert client.get(path, headers=headers).json()["resource_uids"] == form["resource_uids"]
        listing = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/resource-selections"
        from langboard_sdk import CardResources, HttpTransport

        async def sdk_request(method, url, **kwargs):
            return client.request(method, url, headers=headers, **kwargs)

        sdk = CardResources(HttpTransport(SimpleNamespace(request=sdk_request)))
        selected_connections = client.get(listing, headers=headers)
        assert selected_connections.status_code == 200, selected_connections.text
        assert selected_connections.json()["items"][0]["connection_uid"] == connection.get_uid()
        sdk_page = asyncio.run(sdk.list_selections(board[2].get_uid(), card.get_uid()))
        assert sdk_page == selected_connections.json()
        assert sdk_page["next_cursor"] is None
        assert client.put(path, headers=headers, json=form).status_code == 409
        assert client.put(path, headers=headers, json={**form, "expected_revision": 1}).json()["changed"] is False
        assert client.put(path, headers=headers, json={**form, "resource_uids": ["invalid"]}).status_code == 400
        assert (
            client.put(path, headers=headers, json={**form, "resource_uids": form["resource_uids"] * 2}).status_code
            == 400
        )
        with DbSession.atomic() as db:
            binding.granted_capabilities = []
            db.update(binding)
        assert client.put(path, headers=headers, json={**form, "expected_revision": 1}).status_code == 403
        assert client.get(path, headers=headers).json()["revision"] == 1
        with DbSession.atomic() as db:
            connection.state = "disconnected"
            db.update(connection)
        assert client.get(listing, headers=headers).json()["items"][0]["revision"] == 1
        retained = asyncio.run(sdk.list_selections(board[2].get_uid(), card.get_uid()))
        assert retained["items"][0]["connection_uid"] == connection.get_uid()
        assert retained["items"][0]["revision"] == 1
        cleared = client.put(path, headers=headers, json={"resource_uids": [], "expected_revision": 1})
        assert cleared.status_code == 200 and cleared.json()["revision"] == 2, cleared.text
        assert client.get(path, headers=headers).json()["resource_uids"] == []
        with DbSession.atomic() as db:
            card.visibility = "PRIVATE"
            card.owner_user_id = 2
            card.created_by_user_id = 2
            db.update(card)
        assert client.get(path, headers=headers).status_code == 404
        assert client.get(listing, headers=headers).status_code == 404
        assert client.put(path, headers=headers, json={"resource_uids": [], "expected_revision": 2}).status_code == 404
        with DbSession.atomic() as db:
            card.owner_user_id = board[1].id
            card.created_by_user_id = board[1].id
            db.update(card)
        assert client.get(path, headers=headers).status_code == 200
        # A validated API transport cannot impersonate the human session channel for PRIVATE cards.
        payload = {**AuthSecurity.decode_access_token(access), "internal": True}
        api_token = jwt.encode(payload, Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)
        assert (
            client.get(path, headers={"X-Api-Token": api_token, "X-Collaboration-Channel": "human_ui"}).status_code
            == 404
        )
        with DbSession.atomic() as db:
            connection.owner_id = 2
            db.update(connection)
        assert client.get(path, headers=headers).status_code == 403
        assert client.get(listing, headers=headers).json()["items"] == []
        assert client.put(path, headers=headers, json={"resource_uids": [], "expected_revision": 2}).status_code == 403


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_retained_receipt_paging_filters_foreign_connections_before_limit(board):
    from langboard_shared.domain.models import AppConnection, CardAppResourceSelection
    from langboard_shared.domain.services.CardAppResources import list_card_app_resource_selections

    connection, _, _, _, card = prepare(board)
    with DbSession.atomic() as db:
        foreign = AppConnection(app_key="foreign-app", owner_id=2, state="disconnected")
        db.insert(foreign)
        db.insert(
            CardAppResourceSelection(
                card_id=card.id, app_key=foreign.app_key, connection_id=foreign.id, resource_uids=[]
            )
        )
        for index in range(27):
            own = AppConnection(app_key=f"retained-{index}", owner_id=board[1].id, state="disconnected")
            db.insert(own)
            db.insert(
                CardAppResourceSelection(card_id=card.id, app_key=own.app_key, connection_id=own.id, resource_uids=[])
            )
    args = board[1], board[2].id, card.id
    first = list_card_app_resource_selections(*args)
    assert len(first["items"]) == 25 and first["next_cursor"]
    second = list_card_app_resource_selections(*args, after=first["next_cursor"])
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    assert all(item["app_key"] != foreign.app_key for item in first["items"] + second["items"])
    assert {item["connection_uid"] for item in first["items"]}.isdisjoint(
        item["connection_uid"] for item in second["items"]
    )
