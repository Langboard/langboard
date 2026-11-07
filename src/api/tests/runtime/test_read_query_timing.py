"""Native SQL execution, errors and threadpool requests retain isolated telemetry."""

import asyncio
import json
import logging
import os
from threading import Barrier
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langboard.middlewares.ReadTelemetryMiddleware import ReadTelemetryMiddleware
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbConfigHelper import DbConfigHelper
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.db.ReadQueryTiming import (
    ReadQueryTiming,
    ReadTimingQueuePool,
    instrument_read_queries,
    read_query_timing,
)
from sqlalchemy import create_engine, literal, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.pool import NullPool, QueuePool, StaticPool


@pytest.mark.parametrize("database", ["sqlite", "postgres"])
@pytest.mark.parametrize("native", [False, True])
async def test_read_sql_timing_isolated_across_sync_routes_and_errors(database, native, tmp_path, caplog, monkeypatch):
    url = (
        f"sqlite:///{tmp_path / 'timing.db'}"
        if database == "sqlite"
        else os.environ.get("LANGBOARD_SUMMARY_TEST_DATABASE_URL")
    )
    if not url:
        pytest.skip("PostgreSQL test URL not configured")
    engine = instrument_read_queries(create_engine(url, poolclass=ReadTimingQueuePool))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    api = FastAPI()
    api.add_middleware(ReadTelemetryMiddleware)
    barrier = Barrier(2)

    @api.get("/board/{project_uid}")
    def board(project_uid: str):
        barrier.wait(timeout=10)
        if native:
            with DbSession.use(readonly=True) as db:
                for _ in range(int(project_uid)):
                    assert db.exec(SqlBuilder.select.columns(literal("PRIVATE_PARAMETER"))).all() == [
                        ("PRIVATE_PARAMETER",)
                    ]
            return {"count": int(project_uid)}
        with engine.connect() as conn:
            for _ in range(int(project_uid)):
                assert (
                    conn.execute(text("SELECT :value"), {"value": "PRIVATE_PARAMETER"}).scalar_one()
                    == "PRIVATE_PARAMETER"
                )
        return {"count": int(project_uid)}

    @api.get("/dashboard/projects")
    def failing_query():
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text("SELECT * FROM private_missing_timing_table"))
        return {"recovered": True}

    @api.get("/unrelated")
    def unrelated():
        assert read_query_timing.get() is None
        return {"ok": True}

    caplog.set_level(logging.INFO, logger="langboard.http.telemetry")
    try:
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            responses = await asyncio.gather(client.get("/board/2"), client.get("/board/5"))
            recovered = await client.get("/dashboard/projects")
            unrelated_response = await client.get("/unrelated")
        records = [json.loads(record.message) for record in caplog.records if record.name == "langboard.http.telemetry"]
        by_id = {record["request_id"]: record for record in records}
        assert len(records) == 3
        for response, count in zip(responses, [2, 5], strict=True):
            assert response.status_code == 200
            assert response.json() == {"count": count}
            record = by_id[response.headers["x-request-id"]]
            assert record["sql_count"] == count
            assert record["sql_errors"] == 0
            assert record["sql_execute_ms"] >= 0
            assert "sql_execute;dur=" in response.headers["server-timing"]
            assert record["sql_acquire_ms"] > 0
            assert "sql_acquire;dur=" in response.headers["server-timing"]
            for name in ("sql_select_ms", "sql_fetch_ms", "sql_commit_ms", "sql_prepare_ms"):
                assert record[name] > 0 if native else record[name] == 0
                assert f"{name.removesuffix('_ms')};dur=" in response.headers["server-timing"]
        error_record = by_id[recovered.headers["x-request-id"]]
        assert recovered.json() == {"recovered": True}
        assert error_record["sql_count"] == error_record["sql_errors"] == 1
        assert "server-timing" not in unrelated_response.headers
        assert read_query_timing.get() is None
        assert "PRIVATE_PARAMETER" not in json.dumps(records)
        assert "private_missing_timing_table" not in json.dumps(records)
    finally:
        engine.dispose()


def test_connection_acquisition_includes_timeout_without_executing_sql():
    engine = instrument_read_queries(
        create_engine("sqlite://", poolclass=ReadTimingQueuePool, pool_size=1, max_overflow=0, pool_timeout=0.02)
    )
    timing = ReadQueryTiming()
    try:
        with engine.connect():
            token = read_query_timing.set(timing)
            try:
                with pytest.raises(PoolTimeoutError):
                    engine.connect()
            finally:
                read_query_timing.reset(token)
        snapshot = timing.snapshot(close=True)
        assert snapshot["sql_acquire_ms"] >= 10
        assert snapshot["sql_count"] == snapshot["sql_errors"] == 0
        # A failed checkout releases no extra slot and does not poison the pool.
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert timing.snapshot() == snapshot
    finally:
        engine.dispose()


def test_connection_timing_preserves_database_pool_configuration():
    config = DbConfigHelper.create_config("postgresql://unused")
    assert config["poolclass"] is ReadTimingQueuePool
    assert ReadTimingQueuePool.connect is not QueuePool.connect
    assert issubclass(ReadTimingQueuePool, QueuePool)
    assert DbConfigHelper.create_config("sqlite:///:memory:")["poolclass"] is StaticPool
    assert DbConfigHelper.create_config("sqlite:///unused.db")["poolclass"] is NullPool
