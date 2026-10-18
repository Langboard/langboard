"""Opt-in native PostgreSQL diagnostics; skipped runs are never performance proof.

Run with LANGBOARD_WORK_PLAN_LOCK_BENCHMARK=1 and an explicitly disposable
LANGBOARD_FILE_TEST_DATABASE_URL. SQL edits measure DB contention, not API edit
latency. Existing application owners, authorization and locks are not replaced.
"""

import json
import math
import os
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4
import psycopg
import pytest
from langboard.card_workspace.application.work_plan import WorkPlan, WorkPlanRejected, WorkPlanService
from langboard.card_workspace.domain import WorkPlanConflict
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Card,
    CardMetadata,
    CardRelationship,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    EmployeeMembershipPolicy,
    GlobalCardRelationshipType,
    Project,
    ProjectColumn,
    ProjectRole,
    ScimGroup,
    ScimGroupMember,
    UserIdentityLink,
)
from langboard_shared.domain.services import DomainService
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.pool import QueuePool
from src.api.tests.native_fixtures import board as native_board


@pytest.fixture
def lock_cost_board(monkeypatch, tmp_path):
    if os.getenv("LANGBOARD_WORK_PLAN_LOCK_BENCHMARK") != "1":
        pytest.skip("Explicit PostgreSQL lock benchmark opt-in required; no timing evidence")
    if not os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL"):
        pytest.fail("Opted-in benchmark requires an explicitly disposable PostgreSQL URL")
    fixture = native_board.__wrapped__(monkeypatch, SimpleNamespace(param="postgresql-test"), tmp_path)
    try:
        yield next(fixture)
    finally:
        fixture.close()


def percentiles(values):
    ordered = sorted(values)
    return (
        {f"p{p}_ms": round(ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)], 3) for p in (50, 95, 99)}
        if ordered
        else None
    )


def measured_engine(url, schema, workers):
    """Instrument only this disposable engine; keep queue, SQL and commits distinct."""
    local = threading.local()
    active, guard = {}, threading.Lock()

    def finish(operation, kind):
        started = perf_counter()
        try:
            return operation()
        finally:
            sample = getattr(local, "sample", None)
            if sample is not None:
                sample[f"{kind}_ms"] += (perf_counter() - started) * 1000
                tx_start = sample.pop("tx_start", None)
                if tx_start is not None:
                    sample["transaction_ms"] += (perf_counter() - tx_start) * 1000

    class Connection(psycopg.Connection):
        def commit(self):
            return finish(super().commit, "commit")

        def rollback(self):
            return finish(super().rollback, "rollback")

    class Pool(QueuePool):
        def _do_get(self):
            started = perf_counter()
            try:
                return super()._do_get()
            finally:
                sample = getattr(local, "sample", None)
                if sample is not None:
                    sample["pool_acquire_ms"] += (perf_counter() - started) * 1000

    app_name = "work-plan-lock-cost-" + uuid4().hex
    engine = create_engine(
        url,
        poolclass=Pool,
        pool_size=workers,
        max_overflow=0,
        connect_args={
            "application_name": app_name,
            "options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=3000",
        },
    )
    event.listen(engine, "do_connect", lambda _dialect, _record, args, kwargs: Connection.connect(*args, **kwargs))

    @event.listens_for(engine, "checkout")
    def checkout(connection, _record, _proxy):
        sample = getattr(local, "sample", None)
        if sample is not None:
            sample["pid"] = connection.info.backend_pid
            with guard:
                active[sample["pid"]] = sample

    @event.listens_for(engine, "begin")
    def begin(_connection):
        sample = getattr(local, "sample", None)
        if sample is not None:
            sample["tx_start"] = perf_counter()

    @event.listens_for(engine, "before_cursor_execute")
    def before(_connection, _cursor, statement, _parameters, context, _many):
        context.lock_cost_start = perf_counter()
        context.lock_cost_locking = "FOR UPDATE" in statement.upper()

    def sql_done(context):
        sample = getattr(local, "sample", None)
        if sample is not None and context is not None and getattr(context, "lock_cost_start", None):
            elapsed = (perf_counter() - context.lock_cost_start) * 1000
            sample["sql_ms"] += elapsed
            if context.lock_cost_locking:
                sample["locking_sql_ms"] += elapsed
            context.lock_cost_start = None

    event.listen(engine, "after_cursor_execute", lambda _c, _cu, _s, _p, context, _m: sql_done(context))
    event.listen(engine, "handle_error", lambda context: sql_done(context.execution_context))
    return engine, local, active, guard, app_name


@pytest.mark.parametrize("workers", [5, 20, 50])
@pytest.mark.parametrize("pattern", ["same_board", "shared_type", "distinct_types"])
@pytest.mark.parametrize("shape", ["small", "large"])
def test_native_work_plan_lock_cost(lock_cost_board, monkeypatch, tmp_path, request, workers, pattern, shape):
    original = DbEngine.get_main_engine()
    assert original.dialect.name == "postgresql", "SQLite cannot establish lock-cost evidence"
    with original.connect() as db:
        schema = db.execute(text("SELECT current_schema()")).scalar_one()
        server = db.execute(text("SHOW server_version")).scalar_one()
        isolation = db.execute(text("SHOW transaction_isolation")).scalar_one()
    original.dispose()  # Release the fixture bootstrap connection before sizing benchmark backends.
    engine, local, active, guard, app_name = measured_engine(original.url, schema, workers)
    request.addfinalizer(engine.dispose)
    # Same native fixture routing, on a measured fixed-size pool instead of the default 5+10 cap.
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    actor = lock_cost_board[1]
    models = (
        Card,
        CardMetadata,
        Checklist,
        Checkitem,
        CheckitemTimerRecord,
        GlobalCardRelationshipType,
        CardRelationship,
        EmployeeMembershipPolicy,
        UserIdentityLink,
        ScimGroup,
        ScimGroupMember,
    )
    for model in models:
        model.__table__.create(engine, checkfirst=True)
    cards, plans = [], []
    board_count = 1 if pattern == "same_board" else workers
    with DbSession.atomic() as db:
        shared = GlobalCardRelationshipType(parent_name="Contains", child_name="Part of")
        db.insert(shared)
        for board_index in range(board_count):
            project = Project(owner_id=actor.id, title=f"Benchmark {board_index}")
            db.insert(project)
            db.insert(ProjectRole(project_id=project.id, user_id=actor.id, actions=["read", "card_update"]))
            column = ProjectColumn(project_id=project.id, name="Active")
            db.insert(column)
            relation = shared
            if pattern == "distinct_types":
                relation = GlobalCardRelationshipType(parent_name="Contains", child_name="Part of")
                db.insert(relation)
            board_cards = [
                Card(project_id=project.id, project_column_id=column.id, title=f"Card {i}", visibility="SHARED")
                for i in range(2 if shape == "small" else 20)
            ]
            db.insert_all(board_cards)
            anchor = board_cards[0]
            for child in board_cards[1:]:
                db.insert(
                    CardRelationship(card_id_parent=anchor.id, card_id_child=child.id, relationship_type_id=relation.id)
                )
            for index in range(1 if shape == "small" else 4):
                checklist = Checklist(card_id=anchor.id, title=f"Existing {index}")
                db.insert(checklist)
                db.insert_all(
                    [
                        Checkitem(checklist_id=checklist.id, title=f"Step {i}")
                        for i in range(2 if shape == "small" else 12)
                    ]
                )
            cards.append(anchor)
            plans.append(
                WorkPlan(
                    project_uid=project.get_uid(),
                    anchor_card_uid=anchor.get_uid(),
                    new_cards=[{"client_ref": "new:child", "title": "Proposed"}],
                    add_edges=[
                        {
                            "parent_ref": anchor.get_uid(),
                            "child_ref": "new:child",
                            "relationship_type_uid": relation.get_uid(),
                        }
                    ],
                )
            )
    with ExitStack() as connections:
        for _ in range(workers):
            connections.enter_context(engine.connect())
    samples, observer_errors, sampling_intervals = [], [], []
    stop = threading.Event()
    monitor = create_engine(
        original.url,
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-cstatement_timeout=1000"},
    )

    request.addfinalizer(monitor.dispose)

    def observe():
        try:
            with monitor.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
                previous = perf_counter()
                while not stop.wait(0.005):
                    now = perf_counter()
                    sampling_intervals.append((now - previous) * 1000)
                    waiting = (
                        db.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity WHERE application_name=:name "
                                "AND wait_event_type='Lock'"
                            ),
                            {"name": app_name},
                        )
                        .scalars()
                        .all()
                    )
                    with guard:
                        for pid in waiting:
                            if pid in active:
                                active[pid]["sampled_lock_wait_ms"] += (now - previous) * 1000
                                active[pid]["lock_observations"] += 1
                    previous = now
        except Exception as exc:
            observer_errors.append(type(exc).__name__)

    def row_counts():
        with engine.connect() as db:
            return {
                model.__tablename__: db.execute(select(func.count()).select_from(model.__table__)).scalar_one()
                for model in models
            }

    before_counts = row_counts()
    target = int(os.getenv("LANGBOARD_WORK_PLAN_LOCK_SAMPLES", "200"))
    assert 50 <= target <= 5000
    rounds = math.ceil(target / workers)
    start = threading.Barrier(workers)

    def work(worker):
        index = 0 if pattern == "same_board" else worker
        with closing(DomainService()) as service:
            owner = WorkPlanService(actor, service)
            owner.preview(plans[index])  # Excluded per-worker warmup; never a fabricated timing sample.
            start.wait(timeout=30)
            for iteration in range(rounds):
                kind = "sql_edit" if (iteration + worker) % 5 == 0 else "native_preview"
                sample = {
                    key: 0.0
                    for key in (
                        "sql_ms",
                        "locking_sql_ms",
                        "pool_acquire_ms",
                        "transaction_ms",
                        "commit_ms",
                        "rollback_ms",
                        "sampled_lock_wait_ms",
                        "lock_observations",
                    )
                }
                sample.update(kind=kind, outcome="completed")
                local.sample = sample
                began = perf_counter()
                try:
                    if kind == "sql_edit":
                        with DbSession.atomic() as db:
                            db.exec(
                                update(Card)
                                .where(Card.id == cards[index].id)
                                .values(title=f"Edit {worker}:{iteration}", last_change_seq=Card.last_change_seq + 1)
                            )
                    else:
                        assert len(owner.preview(plans[index])["revision"]) == 64
                except Exception as exc:
                    sample["outcome"] = type(exc).__name__
                finally:
                    sample["elapsed_ms"] = (perf_counter() - began) * 1000
                    sample.pop("tx_start", None)
                    with guard:
                        active.pop(sample.pop("pid", None), None)
                    local.sample = None
                    samples.append(sample)

    consistency = {}
    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(work, range(workers)))
        after_counts = row_counts()
        # Actual owner must reject a reviewed revision after a committed edit, before creating anything.
        with closing(DomainService()) as service:
            owner = WorkPlanService(actor, service)
            reviewed = owner.preview(plans[0])["revision"]
            assert owner.preview(plans[0])["revision"] == reviewed
            consistency["stable_revision"] = True
            with DbSession.atomic() as db:
                db.exec(update(Card).where(Card.id == cards[0].id).values(title="Changed after review"))
            with pytest.raises(WorkPlanConflict):
                owner.apply(plans[0], reviewed, "lock-cost-stale")
            consistency["committed_edit_rejects_stale_apply"] = True
            with DbSession.atomic() as db:
                db.exec(
                    update(Card)
                    .where(Card.id == cards[0].id)
                    .values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
                )
            with pytest.raises(WorkPlanRejected, match="CARD_UNAVAILABLE"):
                owner.preview(plans[0])
            consistency["private_card_denied"] = True
        assert after_counts == before_counts == row_counts(), "Preview or rejected apply persisted rows"
        consistency["no_preview_or_rejected_apply_inserts"] = True
    finally:
        stop.set()
        observer.join(timeout=10)
        metrics = (
            "elapsed_ms",
            "pool_acquire_ms",
            "sql_ms",
            "locking_sql_ms",
            "transaction_ms",
            "commit_ms",
            "rollback_ms",
            "sampled_lock_wait_ms",
        )
        report = {
            "status": "complete"
            if len(consistency) == 4
            and samples
            and not observer_errors
            and all(s["outcome"] == "completed" for s in samples)
            else "incomplete",
            "consistency_checks": consistency,
            "postgres_version": server,
            "isolation": isolation,
            "workers": workers,
            "pattern": pattern,
            "shape": shape,
            "pool_size": workers,
            "observer_connection_capacity": 1,
            "max_overflow": 0,
            "pool_prewarmed": True,
            "pure_pool_wait_ms": None,
            "pool_wait_upper_bound": "pool_acquire_ms includes pool bookkeeping; fixed prewarmed pool has no overflow.",
            "rows_before": before_counts,
            "configured_sample_interval_ms": 5,
            "observed_sample_intervals": percentiles(sampling_intervals),
            "outcomes": dict(Counter(s["outcome"] for s in samples)),
            "quantile_population": "All attempted operations, including failures; any failure fails the benchmark.",
            "samples": samples,
            "observer_errors": observer_errors,
            "quantiles": {
                kind: {metric: percentiles([s[metric] for s in samples if s["kind"] == kind]) for metric in metrics}
                for kind in ("native_preview", "sql_edit")
            },
            "warmups_per_worker": 1,
            "mix": "80% native preview / 20% synthetic SQL edits",
            "sample_counts": {kind: sum(s["kind"] == kind for s in samples) for kind in ("native_preview", "sql_edit")},
            "limits": [
                "Pool acquisition includes queue/checkout overhead; not pure pool wait.",
                "Locking SQL elapsed includes query work; sampled lock wait is a resolution-limited estimate.",
                "Transaction time ends after actual DBAPI commit/rollback; commit-call time is separate.",
                "SQL edits are synthetic DB contention, not native edit API latency.",
                "Same-board workers share the anchor; project-versus-card wait attribution is not isolated.",
                "p99 with fewer than 1000 samples per kind has limited tail support.",
                "Rollback-call timing can include connection reset; successful apply/replay is outside this benchmark.",
            ],
        }
        output = Path(os.getenv("LANGBOARD_WORK_PLAN_LOCK_RESULT_DIR", str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        (output / f"work-plan-lock-{workers}-{pattern}-{shape}.json").write_text(json.dumps(report, indent=2) + "\n")
        monitor.dispose()
        engine.dispose()
    assert samples and not observer_errors
    assert all(sample["outcome"] == "completed" for sample in samples)
