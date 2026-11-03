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
from langboard_shared.core.db.ReadQueryTiming import instrument_read_queries, read_query_timing
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


@pytest.mark.parametrize("database", ["sqlite", "postgres"])
async def test_read_sql_timing_isolated_across_sync_routes_and_errors(database, tmp_path, caplog):
    url = (
        f"sqlite:///{tmp_path / 'timing.db'}"
        if database == "sqlite"
        else os.environ.get("LANGBOARD_SUMMARY_TEST_DATABASE_URL")
    )
    if not url:
        pytest.skip("PostgreSQL test URL not configured")
    engine = instrument_read_queries(create_engine(url))
    api = FastAPI()
    api.add_middleware(ReadTelemetryMiddleware)
    barrier = Barrier(2)

    @api.get("/board/{project_uid}")
    def board(project_uid: str):
        barrier.wait(timeout=10)
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
        error_record = by_id[recovered.headers["x-request-id"]]
        assert recovered.json() == {"recovered": True}
        assert error_record["sql_count"] == error_record["sql_errors"] == 1
        assert "server-timing" not in unrelated_response.headers
        assert read_query_timing.get() is None
        assert "PRIVATE_PARAMETER" not in json.dumps(records)
        assert "private_missing_timing_table" not in json.dumps(records)
    finally:
        engine.dispose()
