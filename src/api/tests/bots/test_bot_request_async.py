"""Official async HTTP dispatch must yield while preserving existing retry boundaries."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import httpx
import pytest
from langboard_shared.domain.models.BaseBotModel import BotPlatform
from langboard_shared.domain.models.BotLog import BotLogType
from langboard_shared.tasks.bots.utils.requests.BaseBotRequest import BaseBotRequest


module = importlib.import_module("langboard_shared.tasks.bots.utils.requests.BaseBotRequest")


class Request(BaseBotRequest):
    def create_request_data(self, _log):
        return {"url": "https://bot.invalid/run", "data": {"event": "scheduled"}}


@pytest.fixture
def bot_request(monkeypatch):
    bot = SimpleNamespace(name="Bot", bot_uname="bot", platform=BotPlatform.Default)
    value = Request(bot, "https://bot.invalid", "scheduled", {}, None, None)
    monkeypatch.setattr(value, "_can_dispatch_scope", Mock(return_value=True))
    monkeypatch.setattr(value, "_update_log", AsyncMock())
    monkeypatch.setattr(module, "Env", SimpleNamespace(AI_REQUEST_TRIALS=2, AI_REQUEST_TIMEOUT=10))
    return value


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.asyncio
async def test_dispatch_yields_to_event_loop(bot_request, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(outgoing):
        assert outgoing.headers["X-Test"] == "kept"
        assert outgoing.content == b'{"event":"scheduled"}'
        entered.set()
        await release.wait()
        return httpx.Response(200, text="accepted")

    transport(monkeypatch, handler)
    task = asyncio.create_task(bot_request.request(bot_request.create_request_data(None), {"X-Test": "kept"}, (None, None)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not task.done()
        # This coroutine advances on the same event loop while HTTP remains pending.
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(task, timeout=1)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    bot_request._update_log.assert_awaited_once_with((None, None), BotLogType.Info, "accepted")


@pytest.mark.asyncio
@pytest.mark.parametrize("failures,expected_calls,log_type", [(1, 2, BotLogType.Info), (9, 3, BotLogType.Error)])
async def test_timeout_retry_budget_is_preserved(bot_request, monkeypatch, failures, expected_calls, log_type):
    calls = []

    async def handler(outgoing):
        calls.append(outgoing.content)
        if len(calls) <= failures:
            raise httpx.ReadTimeout("provider timeout", request=outgoing)
        return httpx.Response(200, text="accepted")

    transport(monkeypatch, handler)
    await bot_request.request(bot_request.create_request_data(None), {}, (None, None))
    assert len(calls) == expected_calls
    assert len(set(calls)) == 1
    assert bot_request._update_log.await_args.args[1] == log_type


@pytest.mark.asyncio
async def test_retry_rechecks_scope_before_second_http(bot_request, monkeypatch):
    calls = []

    async def handler(outgoing):
        calls.append(outgoing)
        raise httpx.ReadTimeout("provider timeout", request=outgoing)

    transport(monkeypatch, handler)
    bot_request._can_dispatch_scope.side_effect = [True, False]
    await bot_request.request(bot_request.create_request_data(None), {}, (None, None))
    assert len(calls) == 1
    bot_request._update_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry(bot_request, monkeypatch):
    calls = []

    async def handler(outgoing):
        calls.append(outgoing)
        raise asyncio.CancelledError()

    transport(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        await bot_request.request(bot_request.create_request_data(None), {}, (None, None))
    assert len(calls) == 1
    bot_request._update_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_http_error_is_logged_without_claiming_acceptance(bot_request, monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(503, text="unavailable"))
    await bot_request.request(bot_request.create_request_data(None), {}, (None, None))
    bot_request._update_log.assert_awaited_once_with((None, None), BotLogType.Error, "503: unavailable")
