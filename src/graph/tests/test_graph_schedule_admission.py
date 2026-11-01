"""Native Graph webhook admission must fence duplicate and crashed schedule dispatch."""

import asyncio
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models.BotSchedule import BotSchedule, BotScheduleStatus
from langboard_shared.tasks.bots.utils.BotScheduleRunStore import claim_schedule_run, reserve_schedule_run
from langboard_graph.core.schema.GraphRequestModel import GraphRequestModel
from langboard_graph.routes import RunApi


# The fixture uses actual ORM models and per-test isolated databases.
_fixture_path = Path(__file__).with_name("test_bot_schedule_recovery.py")
if not _fixture_path.exists():
    _fixture_path = Path(__file__).resolve().parents[2] / "api/tests/bots/test_bot_schedule_recovery.py"
_fixture_spec = spec_from_file_location("schedule_recovery_test_support", _fixture_path)
recovery = module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(recovery)


scheduled = recovery.scheduled
card_scheduled = recovery.card_scheduled
runs = recovery.runs
scheduler = recovery.scheduler
transport_module = recovery.transport_module


@pytest.fixture
def webhook(monkeypatch, scheduled):
    executions = []
    monkeypatch.setattr(RunApi, "_acquire_background_slot", AsyncMock(return_value=object()))
    monkeypatch.setattr(RunApi, "_release_background_slot", AsyncMock())

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            executions.append("execution")

    monkeypatch.setattr(RunApi, "GraphRunner", Runner)
    service = SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None))
    queues = []

    async def post(request):
        payload = GraphRequestModel.model_validate(json.loads(request.content))
        queue = BackgroundTasks()
        try:
            response = await RunApi.webhook_run_graph(payload.session_id, payload, queue, service)
        except HTTPException as exc:
            return httpx.Response(exc.status_code, json={"detail": exc.detail})
        queues.append(queue)
        return httpx.Response(response.status_code, content=response.body)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(post), **kwargs)
    )
    return executions, queues, post


@pytest.mark.asyncio
async def test_scheduler_503_restart_then_one_native_graph_admission(scheduled, webhook, monkeypatch):
    from datetime import timedelta

    executions, queues, post = webhook
    count = 0

    async def reject_once(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(503)
        return await post(request)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(reject_once), **kwargs)
    )
    await scheduler._run_scheduler(*scheduled)
    run = runs()[0]
    assert run.state == "pending" and run.attempt == 1
    with DbSession.atomic() as db:
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    await scheduler.recover_due_onetime_schedules()
    assert runs()[0].state == "accepted" and runs()[0].attempt == 2
    assert len(queues) == 1 and len(queues[0].tasks) == 1
    assert executions == []  # Acceptance is not graph execution success.
    await queues[0]()
    await scheduler.recover_due_onetime_schedules()
    assert executions == ["execution"] and len(queues) == 1


def payload_for(scheduled):
    from langboard_shared.domain.models import Project
    from langboard_shared.helpers import BotHelper
    from langboard_shared.tasks.bots.utils.requests.DefaultRequest import DefaultRequest

    bot, schedule, association = scheduled
    model = BotHelper.get_target_model_by_bot_model("schedule", association)
    with DbSession.atomic() as db:
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == model.project_id)).first()
    run = reserve_schedule_run(schedule, association, now=SafeDateTime.now())
    lease = claim_schedule_run(run.id, now=SafeDateTime.now())
    execution = {"occurrence_key": lease.occurrence_key, "token": lease.token, "attempt": lease.attempt}
    data = {
        "project_uid": project.get_uid(),
        "project_column_uid": (
            model.get_uid() if model.__tablename__ == "project_column" else model.project_column_id.to_short_code()
        ),
        "scope": model.__tablename__,
        "schedule_execution": execution,
    }
    from langboard_shared.domain.models.BotLog import BotLog, BotLogType

    if model.__tablename__ == "card":
        data["card_uid"] = model.get_uid()
    log = BotLog(bot_id=bot.id, log_type=BotLogType.Info)
    with DbSession.atomic() as db:
        db.insert(log)
        log_class = BotHelper.get_bot_model_class("log", model.__tablename__)
        scope_log = log_class(**{"bot_log_id": log.id, f"{model.__tablename__}_id": model.id})
        db.insert(scope_log)
    payload = DefaultRequest(bot, "", "bot_cron_scheduled", data, project, model).create_request_data((log, scope_log))[
        "data"
    ]
    return GraphRequestModel.model_validate(payload)


@pytest.mark.asyncio
async def test_duplicate_native_handlers_enqueue_once(scheduled, webhook):
    executions, queues, _ = webhook
    payload = payload_for(scheduled)
    service = SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None))
    first, second = BackgroundTasks(), BackgroundTasks()
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def dispatch(queue):
        barrier.wait(timeout=5)
        try:
            return asyncio.run(RunApi.webhook_run_graph(payload.session_id, payload, queue, service))
        except HTTPException as exc:
            assert exc.status_code == 409

    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(dispatch, (first, second)))
    assert len(first.tasks) + len(second.tasks) == 1
    assert runs()[0].state == "accepted"
    await first()
    await second()
    assert executions == ["execution"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["bot", "scope", "token", "stopped", "edited", "credentials"])
async def test_swapped_or_revoked_context_rejected(scheduled, webhook, mutation):
    payload = payload_for(scheduled)
    if mutation == "bot":
        payload.uid = "not-the-owner"
    elif mutation == "scope":
        payload.tweaks["rest_data"]["project_column_uid"] = "wrong-source"
    elif mutation == "token":
        payload.schedule_execution.token = "f" * 32
    elif mutation == "credentials":
        from langboard_shared.domain.models import Bot

        with DbSession.atomic() as db:
            bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == scheduled[0].id)).first()
            bot.app_api_token = "rotated-test-token"
            db.update(bot)
    else:
        with DbSession.atomic() as db:
            schedule = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == scheduled[1].id)).first()
            if mutation == "stopped":
                schedule.status = BotScheduleStatus.Stopped
            else:
                schedule.interval_str = "0 9 * * *"
            db.update(schedule)
    queue = BackgroundTasks()
    try:
        response = await RunApi.webhook_run_graph(
            payload.session_id,
            payload,
            queue,
            SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None)),
        )
    except (HTTPException, ValueError):
        pass
    else:
        assert response.status_code >= 400
    assert queue.tasks == []
    assert webhook[0] == []


@pytest.mark.asyncio
async def test_crash_between_reservation_and_enqueue_cannot_replay(scheduled, webhook, monkeypatch):
    from langboard_shared.tasks.bots.utils.BotScheduleRunStore import admit_graph_schedule

    payload = payload_for(scheduled)
    assert admit_graph_schedule(payload) is True
    assert runs()[0].state == "uncertain"
    with pytest.raises(HTTPException) as failure:
        await RunApi.webhook_run_graph(
            payload.session_id,
            payload,
            BackgroundTasks(),
            SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None)),
        )
    assert failure.value.status_code == 409
    assert runs()[0].error_code == "graph_admission_reserved"
    assert webhook[0] == []


@pytest.mark.asyncio
async def test_queue_registration_failure_remains_uncertain(scheduled, webhook):
    class BrokenQueue:
        def add_task(self, *args):
            raise RuntimeError("queue registration failed")

    payload = payload_for(scheduled)
    with pytest.raises(HTTPException):
        await RunApi.webhook_run_graph(
            payload.session_id,
            payload,
            BrokenQueue(),
            SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None)),
        )
    assert runs()[0].state == "uncertain" and runs()[0].error_code == "graph_admission_reserved"
    assert webhook[0] == []


@pytest.mark.asyncio
async def test_new_scheduler_process_recovers_persisted_rejection_over_native_http(scheduled, webhook, monkeypatch):
    """A fresh interpreter consumes the durable retry and reaches the native Graph handler."""
    import os
    import subprocess
    import sys
    import threading
    from datetime import timedelta
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from langboard_shared.core.db.DbEngine import DbEngine

    async def reject(request):
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(reject), **kwargs)
    )
    await scheduler._run_scheduler(*scheduled)
    run = runs()[0]
    assert run.state == "pending" and run.attempt == 1
    with DbSession.atomic() as db:
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    queues = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = GraphRequestModel.model_validate_json(self.rfile.read(int(self.headers["Content-Length"])))
            queue = BackgroundTasks()
            response = asyncio.run(
                RunApi.webhook_run_graph(
                    payload.session_id,
                    payload,
                    queue,
                    SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None)),
                )
            )
            queues.append(queue)
            self.send_response(response.status_code)
            self.end_headers()
            self.wfile.write(response.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    engine = DbEngine.get_main_engine()
    environment = os.environ.copy()
    environment["SCHEDULE_TEST_URL"] = engine.url.render_as_string(hide_password=False)
    # PostgreSQL test engine carries its isolated search path in connection options.
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            environment["SCHEDULE_TEST_SCHEMA"] = connection.exec_driver_sql("SELECT current_schema()").scalar()
    environment["SCHEDULE_TEST_GRAPH_URL"] = f"http://127.0.0.1:{server.server_port}"
    code = """
import asyncio, faulthandler, os
faulthandler.dump_traceback_later(15)
from crontab import CronTab
from sqlalchemy import create_engine
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.services import DomainService
from langboard_shared.tasks.bots.BotScheduleTask import run_scheduled_bots_cron, BotScheduleHelper, ProjectBotPublisher
from langboard_shared.tasks.bots.utils.requests.BaseBotRequest import BaseBotRequest
schema = os.environ.get("SCHEDULE_TEST_SCHEMA")
engine = create_engine(os.environ["SCHEDULE_TEST_URL"], connect_args={"options": "-csearch_path=" + schema} if schema else {})
DbEngine.get_main_engine = staticmethod(lambda: engine)
DbEngine.get_readonly_engine = staticmethod(lambda: engine)
from langboard_shared.tasks.bots.utils.requests.Utils import _REQUEST_MAP
from langboard_shared.tasks.bots.utils.requests.DefaultRequest import DefaultRequest
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
_REQUEST_MAP[BotPlatform.Default][BotPlatformRunningType.Default] = (DefaultRequest, os.environ["SCHEDULE_TEST_GRAPH_URL"])
BaseBotRequest._get_bot_request_headers = lambda self: {}
ProjectBotPublisher.log_created = lambda *args: None
ProjectBotPublisher.log_stack_added = lambda *args: None
ProjectBotPublisher.rescheduled = lambda *args: None
BotScheduleHelper.utils.get_cron = lambda: CronTab(tab="")
BotScheduleHelper.utils.save_cron = lambda cron: None
asyncio.run(run_scheduled_bots_cron("recover"))
engine.dispose()
faulthandler.cancel_dump_traceback_later()
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 0, result.stderr
        assert runs()[0].state == "accepted" and runs()[0].attempt == 2
        assert len(queues) == 1 and len(queues[0].tasks) == 1
        assert webhook[0] == []
        await queues[0]()
        assert webhook[0] == ["execution"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_graph_rechecks_current_private_card_permission(card_scheduled, webhook):
    from langboard_shared.domain.models import Card

    payload = payload_for(card_scheduled)
    with DbSession.atomic() as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == 7)).first()
        card.visibility = "PRIVATE"
        card.owner_user_id = 1
        db.update(card)
    queue = BackgroundTasks()
    with pytest.raises(HTTPException) as failure:
        await RunApi.webhook_run_graph(
            payload.session_id,
            payload,
            queue,
            SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None)),
        )
    assert failure.value.status_code == 409 and queue.tasks == []


@pytest.mark.asyncio
async def test_synchronous_route_cannot_bypass_scheduled_admission(scheduled, webhook):
    payload = payload_for(scheduled)
    with pytest.raises(HTTPException) as failure:
        await RunApi.run_graph(payload.session_id, payload)
    assert failure.value.status_code == 400 and webhook[0] == []


@pytest.mark.asyncio
async def test_admission_database_error_releases_background_slot_once(scheduled, webhook, monkeypatch):
    from langboard_shared.tasks.bots.utils import BotScheduleRunStore

    payload = payload_for(scheduled)

    def fail_admission(request):
        raise RuntimeError("admission database unavailable")

    monkeypatch.setattr(BotScheduleRunStore, "admit_graph_schedule", fail_admission)
    queue = BackgroundTasks()
    with pytest.raises(RuntimeError, match="admission database unavailable"):
        await RunApi.webhook_run_graph(
            payload.session_id,
            payload,
            queue,
            SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda uid: None)),
        )
    RunApi._release_background_slot.assert_awaited_once()
    assert queue.tasks == [] and webhook[0] == []
