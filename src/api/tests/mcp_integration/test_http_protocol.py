"""Exercise legacy and modern clients over real stateless HTTP transport."""

import asyncio
import multiprocessing
import os
import socket
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import httpx2
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from langboard.mcp_integration.Server import _create_fastmcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette


class FixtureGroupMiddleware:
    """Provide an isolated group fixture; this is not production authentication proof."""

    def __init__(self, app, tools=("record",)):
        self.app = app
        self.tools = list(tools)

    async def __call__(self, scope, receive, send):
        token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=self.tools)})
        try:
            await self.app(scope, receive, send)
        finally:
            mcp_auth_context.reset(token)


@pytest.mark.parametrize("replica_count", [1, 2])
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_stateless_http_negotiation_discovery_and_write(mode, replica_count, monkeypatch):
    # Uvicorn's SSE shutdown flag is process-global and can outlive another server.
    monkeypatch.setattr(AppStatus, "should_exit", False)
    existing_tasks = asyncio.all_tasks()
    writes = []

    def record(value: int) -> dict[str, int]:
        if value < 0:
            raise ValueError("private-transport-error-detail")
        writes.append(value)
        return {"value": value}

    def example() -> str:
        return "Example resource"

    def example_prompt() -> str:
        return "Example prompt"

    # Independent FastMCP applications model transport replicas in one process.
    # This does not establish production authentication or cross-process proof.
    replicas = []
    for _ in range(replica_count):
        replica = _create_fastmcp()
        replica.tool(record)
        replica.resource("fixture://example")(example)
        replica.prompt(example_prompt)
        replica_app = replica.http_app(path="/stream", stateless_http=True, allowed_hosts=["127.0.0.1:*", "127.0.0.1"])
        replica_app.add_middleware(FixtureGroupMiddleware)
        replicas.append(replica_app)

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for replica_app in replicas:
                await stack.enter_async_context(replica_app.router.lifespan_context(replica_app))
            yield

    arrivals = []

    class AlternateReplicas:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            index = len(arrivals) % replica_count
            arrivals.append(index)
            await replicas[index](scope, receive, send)

    app = Starlette(lifespan=lifespan)
    app.add_middleware(AlternateReplicas)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    http_server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    task = asyncio.create_task(http_server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if http_server.started or task.done():
                break
            await asyncio.sleep(0.01)
        assert http_server.started
        async with Client(f"http://127.0.0.1:{port}/stream", mode=mode) as client:
            assert client.protocol_version == ("2025-11-25" if mode == "legacy" else mode)
            assert [tool.name for tool in await client.list_tools()] == ["record"]
            assert [str(resource.uri) for resource in await client.list_resources()] == ["fixture://example"]
            assert [prompt.name for prompt in await client.list_prompts()] == ["example_prompt"]
            # Numeric strings must not be coerced by either HTTP protocol mode.
            with pytest.raises(ToolError):
                await client.call_tool("record", {"value": "7"})
            assert writes == []
            # Handler exceptions must be masked in the actual wire response.
            failure = await client.call_tool("record", {"value": -1}, raise_on_error=False)
            assert failure.is_error
            assert failure.content
            assert "private-transport-error-detail" not in str(failure.content)
            assert writes == []
            result = await client.call_tool("record", {"value": 7})
            assert result.structured_content == {"value": 7}
            assert writes == [7]
            # Two consecutive writes cross replicas without initializing again.
            write_start = len(arrivals)
            for value in (8, 9):
                result = await client.call_tool("record", {"value": value})
                assert result.structured_content == {"value": value}
            assert writes == [7, 8, 9]
            assert len(arrivals[write_start:]) == 2
            assert set(arrivals[write_start:]) == set(range(replica_count))
        # A new client connection also works without an old session header.
        async with Client(f"http://127.0.0.1:{port}/stream", mode=mode) as client:
            assert [tool.name for tool in await client.list_tools()] == ["record"]
        assert set(arrivals) == set(range(replica_count))
    finally:
        http_server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()
        # sse-starlette's loop-local shutdown watcher can outlive uvicorn.
        watchers = [
            pending
            for pending in asyncio.all_tasks() - existing_tasks
            if getattr(pending.get_coro(), "__name__", "") == "_shutdown_watcher"
        ]
        for pending in watchers:
            pending.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)


def _serve_process_replica(listener: socket.socket, records: Path) -> None:
    server = _create_fastmcp()

    @server.tool
    def record(value: int) -> dict[str, int]:
        with records.open("a") as output:
            output.write(f"{value}\n")
        return {"value": value, "pid": os.getpid()}

    @server.tool
    def read_records() -> dict[str, list[int]]:
        return {"values": [int(line) for line in records.read_text().splitlines()]}

    app = server.http_app(path="/stream", stateless_http=True, allowed_hosts=["127.0.0.1:*"])
    app.add_middleware(FixtureGroupMiddleware, tools=("record", "read_records"))
    uvicorn.Server(uvicorn.Config(app, log_level="critical")).run(sockets=[listener])


@asynccontextmanager
async def _process_replica(records: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        process = multiprocessing.get_context("spawn").Process(target=_serve_process_replica, args=(listener, records))
        process.start()
    try:
        for _ in range(200):
            if not process.is_alive():
                pytest.fail(f"Replica stopped before serving HTTP, exit code {process.exitcode}")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.05)
                continue
            writer.close()
            await writer.wait_closed()
            break
        else:
            pytest.fail("Replica did not begin listening")
        yield port, process.pid
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            process.kill()
        await asyncio.to_thread(process.join, 5)
        process.close()


@pytest.mark.parametrize(("mode", "version"), [("legacy", "2025-11-25"), ("2026-07-28", "2026-07-28")])
async def test_stateless_http_switches_processes_without_reinitializing(mode, version, tmp_path):
    records = tmp_path / "values.txt"
    records.touch()
    async with AsyncExitStack() as stack:
        first, second = [await stack.enter_async_context(_process_replica(records)) for _ in range(2)]
        arrivals: list[int] = []

        class ReplicaTransport(httpx2.AsyncHTTPTransport):
            async def handle_async_request(self, request):
                port, _ = (first, second)[len(arrivals) % 2]
                arrivals.append(port)
                request.url = request.url.copy_with(port=port)
                request.headers["host"] = f"127.0.0.1:{port}"
                return await super().handle_async_request(request)

        def create_http_client(**kwargs):
            return httpx2.AsyncClient(transport=ReplicaTransport(), **kwargs)

        transport = StreamableHttpTransport(
            f"http://127.0.0.1:{first[0]}/stream", httpx_client_factory=create_http_client
        )
        async with Client(transport, mode=mode, timeout=10) as client:
            assert client.protocol_version == version
            assert {tool.name for tool in await client.list_tools()} == {"record", "read_records"}
            offset = len(arrivals)
            writes = [await client.call_tool("record", {"value": value}) for value in (7, 8)]
            assert {result.structured_content["pid"] for result in writes} == {first[1], second[1]}
            assert set(arrivals[offset:]) == {first[0], second[0]}
            read = await client.call_tool("read_records", {})
            assert read.structured_content == {"values": [7, 8]}
