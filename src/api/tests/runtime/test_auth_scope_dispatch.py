"""Verified actor projection must not queue behind unrelated synchronous work."""

import asyncio
from threading import Event
import anyio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langboard_shared.security.Auth import Auth


async def test_auth_scope_remains_available_when_threadpool_is_full():
    actor = object()
    app = FastAPI()

    @app.get("/actor")
    async def actor_route(value=Auth.scope("all")):
        return {"same_actor": value is actor}

    async def authenticated_app(scope, receive, send):
        scope["auth"] = actor
        await app(scope, receive, send)

    limiter = anyio.to_thread.current_default_thread_limiter()
    old_limit = limiter.total_tokens
    release = Event()
    ready = asyncio.Event()

    def occupying_work():
        anyio.from_thread.run_sync(ready.set)
        if not release.wait(timeout=5):
            raise AssertionError("Test cleanup did not release the worker")

    limiter.total_tokens = 1
    worker = asyncio.create_task(anyio.to_thread.run_sync(occupying_work))
    pending = None
    try:
        await asyncio.wait_for(ready.wait(), timeout=3)
        async with AsyncClient(transport=ASGITransport(app=authenticated_app), base_url="http://test") as client:
            pending = asyncio.create_task(client.get("/actor"))
            try:
                response = await asyncio.wait_for(asyncio.shield(pending), timeout=1)
            finally:
                release.set()
                await pending
            assert response.status_code == 200
            assert response.json() == {"same_actor": True}
    finally:
        release.set()
        await worker
        limiter.total_tokens = old_limit


if __name__ == "__main__":
    asyncio.run(test_auth_scope_remains_available_when_threadpool_is_full())
    print("AUTH_SCOPE_DISPATCH_PASS")
