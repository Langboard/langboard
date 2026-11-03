"""Loopback-only browser fixture using the native My Work HTTP/service/repository path."""

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langboard.routes.dashboard.DashboardApi import get_my_work
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import User


def main():
    test_path = Path(__file__).resolve().parents[1] / "test_dashboard_my_work.py"
    spec = importlib.util.spec_from_file_location("native_my_work_tests", test_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    patch = pytest.MonkeyPatch()
    fixture = module.my_work_db.__wrapped__(patch)
    service, actor, add, _, _ = next(fixture)
    try:
        for index in range(61):
            add(f"Assigned task {index:02d}")
        add("Review task", project_column_id=41, updated_at=SafeDateTime.fromisoformat("2026-10-20T00:00:00Z"))
        add("Foreign project task", project_id=11, project_column_id=45)
        add("Today task", deadline_at=SafeDateTime.fromisoformat("2026-10-23T04:00:00Z"))
        add("Overdue task", deadline_at=SafeDateTime.fromisoformat("2026-10-21T04:00:00Z"))
        add("Hidden private task", visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://127.0.0.1:4192"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.middleware("http")
        async def fixture_actor(request, call_next):
            uid = int(request.headers.get("x-fixture-actor", "1"))
            request.scope["auth"] = actor if uid == 1 else User(
                id=uid, firstname="Other", lastname="Fixture", email="other@example.invalid", password="fixture"
            )
            request.scope["collaboration_channel"] = CollaborationChannel.HumanUI
            return await call_next(request)

        dependency = inspect.signature(get_my_work).parameters["service"].default.dependency
        app.dependency_overrides[dependency] = lambda: SimpleNamespace(
            card=service, notification=SimpleNamespace(get_mentioned_card_ids=lambda _: [])
        )
        app.get("/dashboard/work/my")(get_my_work)

        @app.get("/fixture")
        def read_fixture():
            return {"projects": [SnowflakeID(i).to_short_code() for i in (10, 11)], "actor": actor.get_uid()}

        @app.exception_handler(Exception)
        async def fixture_error(request: Request, exc):
            return JSONResponse(status_code=getattr(exc, "status_code", 500), content={"error": str(exc)})

        uvicorn.run(app, host="127.0.0.1", port=5392, log_level="warning")
    finally:
        fixture.close()
        patch.undo()


if __name__ == "__main__":
    main()
