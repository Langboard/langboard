"""Persisted gate contracts and real native movement, without external dispatch."""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
import test_decision_flow as decision_tests
from langboard.routes.board.BoardDecisionGateApi import ConfigureDecisionGateForm
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain import models as m
from langboard_shared.domain.services.DecisionGate import DecisionGate, DecisionGateBlocked, fence_move_authority
from sqlalchemy import select, text, update
from test_decision_flow import create, respond


base_native = decision_tests.native


@pytest.fixture
def gate_native(base_native, monkeypatch):
    native = base_native
    engine, flow, users, board, foreign, card = native
    from sqlalchemy import inspect

    existing = set(inspect(engine).get_table_names())
    names = {
        "card_decision_gate",
        "decision_option",
        "setting_role",
        "board_team",
        "board_team_member",
        "workflow_stage_definition",
        "card_relationship",
        "global_card_relationship_type",
        "card_app_ownership",
        "card_change_receipt",
        "workflow_effect_run",
        "workflow_rule",
        "card_assigned_user",
        "card_assigned_project_label",
        "card_attachment",
        "card_content_block",
        "card_comment",
        "checklist",
        "checkitem",
        "project_activity",
        "project_label",
        "whisper_thread",
        "project_column_bot_scope",
    }
    required = {m.User.metadata.tables[name] for name in names}
    pending = list(required)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            if foreign_key.column.table not in required:
                required.add(foreign_key.column.table)
                pending.append(foreign_key.column.table)
    m.User.metadata.create_all(engine, tables=list(required))
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE SEQUENCE IF NOT EXISTS content_change_seq"))
    with DbSession.atomic() as db:
        stage = m.WorkflowStageDefinition(key="gate_done", name="Done")
        db.insert(stage)
        target = m.ProjectColumn(project_id=board.id, name="Done", order=1, workflow_stage=stage.key)
        db.insert(target)
        old = db.exec(
            SqlBuilder.select.table(m.ProjectColumn).where(m.ProjectColumn.id == card.project_column_id)
        ).first()
    # Keep actual move SQL and stage effects; suppress native external delivery.
    svc = flow.service.card
    module = importlib.import_module(svc.__module__)

    @contextmanager
    def execution():
        with DbSession.atomic() as db:
            yield SimpleNamespace(db=db, watch_card_and_dependents=lambda *_: None)

    monkeypatch.setattr(module, "execution_readiness_uow", execution)
    monkeypatch.setattr(svc, "notify_order_changed", lambda *_: None)
    monkeypatch.setattr(module.CardPublisher, "updated", lambda *_: None)
    monkeypatch.setattr(svc, "_invalidate_source_card_mirrors", lambda *_: None)
    yield native, DecisionGate(flow.service), old, target
    extras = [table for table in m.User.metadata.sorted_tables if table.name not in existing]
    m.User.metadata.drop_all(engine, tables=extras)


def bind(fixture, case):
    native, gate, _, target = fixture
    _, _, users, board, _, card = native
    return gate.configure(
        users[0],
        board.get_uid(),
        card.get_uid(),
        ConfigureDecisionGateForm(
            target_stage=target.workflow_stage,
            decision_uid=case["uid"],
            round_id=case["round_id"],
            target_revision=case["target_revision"],
            policy_revision=case["policy_revision"],
        ),
    )


def move(fixture, target=None):
    native, _, _, default = fixture
    _, flow, users, board, _, card = native
    return flow.service.card.change_order(users[0], board, card, 0, target or default)


def current(fixture):
    native = fixture[0]
    with DbSession.atomic() as db:
        return db.exec(SqlBuilder.select.table(m.Card).where(m.Card.id == native[-1].id)).first()


def test_unconfigured_move_and_read_remove(gate_native):
    native, gate, _, target = gate_native
    _, _, users, board, _, card = native
    assert gate.read(users[0], board.get_uid(), card.get_uid()) == {"gates": []}
    assert move(gate_native) is True
    assert current(gate_native).project_column_id == target.id
    assert gate.remove(users[0], board.get_uid(), card.get_uid(), target.workflow_stage) == {"removed": False}


@pytest.mark.parametrize(
    "choice,reason",
    [(None, "proposal_pending"), ("reject", "proposal_rejected"), ("needs_info", "proposal_needs_info")],
)
def test_pending_negative_denies_without_mutation(gate_native, choice, reason):
    native, _, old, _ = gate_native
    case = create(native)
    if choice:
        from test_decision_flow import form

        respond(native, case, payload=form(case, choice=choice))
    bind(gate_native, case)
    with pytest.raises(DecisionGateBlocked) as exc:
        move(gate_native)
    assert exc.value.reason == reason
    assert current(gate_native).project_column_id == old.id


def test_passed_real_evaluation_and_same_stage_parity(gate_native):
    native, gate, old, target = gate_native
    case = create(native)
    bind(gate_native, case)
    respond(native, case)
    assert move(gate_native) is True
    # The target revision changed on entry. Same-stage reorder still succeeds.
    assert move(gate_native) is True
    assert current(gate_native).project_column_id == target.id


@pytest.mark.parametrize("stale", ["target", "round", "policy", "acl", "member", "cached"])
def test_stale_authority_and_cached_pass_never_authorize(gate_native, stale):
    native, _, old, _ = gate_native
    engine, _, users, _, _, card = native
    case = create(native)
    bind(gate_native, case)
    respond(native, case)
    with engine.begin() as connection:
        if stale == "target":
            connection.execute(update(m.Card).where(m.Card.id == card.id).values(title="Changed"))
        elif stale in ("round", "policy"):
            connection.execute(
                update(m.DecisionCase)
                .where(m.DecisionCase.id == m.DecisionCase.id)
                .values(**({"round_id": "changed"} if stale == "round" else {"policy_revision": "changed"}))
            )
        elif stale == "acl":
            connection.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
        elif stale == "member":
            connection.execute(update(m.User).where(m.User.id == users[1].id).values(deleted_at=m.User.updated_at))
        else:
            connection.execute(m.DecisionResponse.__table__.delete())
    with pytest.raises(DecisionGateBlocked):
        move(gate_native)
    assert current(gate_native).project_column_id == old.id


def test_poll_rejected_and_binding_requires_native_update(gate_native):
    from langboard_shared.domain.services.DecisionFlow import DecisionDenied

    native, gate, _, target = gate_native
    _, _, users, board, _, card = native
    case = create(native)
    data = ConfigureDecisionGateForm(
        target_stage=target.workflow_stage,
        decision_uid=case["uid"],
        round_id=case["round_id"],
        target_revision=case["target_revision"],
        policy_revision=case["policy_revision"],
    )
    with pytest.raises(DecisionDenied):
        gate.configure(users[1], board.get_uid(), card.get_uid(), data)
    with DbSession.atomic() as db:
        db.exec(update(m.DecisionCase).values(source_kind="poll"))
    with pytest.raises(ValueError, match="proposal"):
        bind(gate_native, case)


def test_bot_cannot_skip_proposal_gate(gate_native):
    native, _, old, target = gate_native
    _, flow, _, board, _, card = native
    bind(gate_native, create(native))
    with pytest.raises(DecisionGateBlocked, match="proposal_pending"):
        flow.service.card.change_order(m.Bot.model_construct(name="test"), board, card, 0, target)
    assert current(gate_native).project_column_id == old.id


def test_move_transaction_rolls_back_prior_family_writes_and_effects(gate_native):
    native, gate, old, target = gate_native
    _, _, users, board, _, card = native
    bind(gate_native, create(native))
    callbacks = []
    with pytest.raises(DecisionGateBlocked):
        with DbSession.atomic() as db:
            fence_move_authority(db)
            sibling = m.Card(project_id=board.id, project_column_id=old.id, title="Sibling")
            db.insert(sibling)
            sibling.project_column_id = target.id
            db.update(sibling)
            db.after_commit(lambda: callbacks.append("effect"))
            gate.require(db, users[0], board, card, old, target)
    with DbSession.atomic() as db:
        assert db.exec(select(m.Card.id).where(m.Card.title == "Sibling")).first() is None
    assert callbacks == []


def test_native_family_descendant_denial_rolls_back_parent(gate_native):
    native, gate, old, target = gate_native
    engine, flow, users, board, foreign, parent = native
    with DbSession.atomic() as db:
        stage = db.exec(
            SqlBuilder.select.table(m.WorkflowStageDefinition).where(
                m.WorkflowStageDefinition.key == target.workflow_stage
            )
        ).first()
        stage.confirm_descendant_move = True
        db.update(stage)
        child = m.Card(project_id=board.id, project_column_id=old.id, title="Gated child", visibility="SHARED")
        db.insert(child)
        relation = m.GlobalCardRelationshipType(
            parent_name="contains", child_name="inside", machine_semantic="contains", is_system_default=True
        )
        db.insert(relation)
        db.insert(
            m.CardRelationship(relationship_type_id=relation.id, card_id_parent=parent.id, card_id_child=child.id)
        )
    child_native = (engine, flow, users, board, foreign, child)
    child_fixture = (child_native, gate, old, target)
    bind(child_fixture, create(child_native))
    confirmation = move(gate_native)
    assert confirmation["confirmation_required"]
    with pytest.raises(DecisionGateBlocked, match="proposal_pending"):
        flow.service.card.change_order(
            users[0],
            board,
            parent,
            0,
            target,
            move_descendants=True,
            confirmation_revision=confirmation["confirmation_revision"],
        )
    assert current(gate_native).project_column_id == old.id
    assert current(child_fixture).project_column_id == old.id
    with DbSession.atomic() as db:
        assert db.exec(select(m.CardChangeReceipt.id).where(m.CardChangeReceipt.card_id == parent.id)).first() is None


def test_native_archive_target_respects_gate(gate_native):
    native, _, old, target = gate_native
    engine = native[0]
    with engine.begin() as connection:
        connection.execute(update(m.ProjectColumn).where(m.ProjectColumn.id == target.id).values(is_archive=True))
    bind(gate_native, create(native))
    with pytest.raises(DecisionGateBlocked, match="proposal_pending"):
        move(gate_native)
    assert current(gate_native).project_column_id == old.id
    assert current(gate_native).archived_at is None


def test_rest_and_mcp_structured_failure_mapping(gate_native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.mcp_tools import CardMcp
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardCardApi, BoardDecisionGateApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    native, _, old, target = gate_native
    _, flow, users, board, _, card = native
    case = create(native)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "decision-gate-native-key-at-least-32-bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    base = f"/board/{board.get_uid()}/card/{card.get_uid()}"
    with TestClient(app) as client:
        access, refresh = AuthSecurity.authenticate(users[0].id)
        headers = {"Authorization": f"Bearer {access}"}
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        body = dict(
            target_stage=target.workflow_stage,
            decision_uid=case["uid"],
            round_id=case["round_id"],
            target_revision=case["target_revision"],
            policy_revision=case["policy_revision"],
        )
        configured = client.put(base + "/decision-gate", headers=headers, json=body)
        assert configured.status_code == 200, configured.text
        assert client.get(base + "/decision-gate", headers=headers).json()["gates"][0]["decision_uid"] == case["uid"]
        denied = client.put(base + "/order", headers=headers, json={"order": 0, "parent_uid": target.get_uid()})
        assert denied.status_code == 409, denied.text
        assert denied.json()["reason"] == "proposal_pending"
        assert "confirmation_required" not in denied.json()
        operation = CardMcp.change_card_order_or_move_column
        while hasattr(operation, "__wrapped__"):
            operation = operation.__wrapped__
        result = operation(board.get_uid(), card.get_uid(), 0, users[0], flow.service, column_uid=target.get_uid())
        assert result == denied.json()
        assert client.delete(base + "/decision-gate/" + target.workflow_stage, headers=headers).status_code == 200
    assert current(gate_native).project_column_id == old.id


def test_gate_migration_refuses_retained_binding_downgrade(gate_native):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    native, _, _, _ = gate_native
    bind(gate_native, create(native))
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024110000-7fab890c1234.py"
    spec = importlib.util.spec_from_file_location("gate_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with pytest.raises(RuntimeError, match="retained"):
        with native[0].begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()


def test_current_team_membership_is_required(gate_native):
    from sqlalchemy import delete

    native, _, old, _ = gate_native
    engine, _, users, board, _, _ = native
    case = create(native)
    bind(gate_native, case)
    respond(native, case)
    with DbSession.atomic() as db:
        organization = m.Organization(name="Native", slug="gate", owner_user_id=users[0].id)
        db.insert(organization)
        db.exec(update(m.Project).where(m.Project.id == board.id).values(organization_id=organization.id))
        team = m.BoardTeam(project_id=board.id, name="Approvers")
        db.insert(team)
        db.insert(m.BoardTeamMember(team_id=team.id, user_id=users[1].id))
        source = {"kind": "team", "uid": team.get_uid()}
        snapshot = {
            "project_uid": board.get_uid(),
            "actor_uids": [users[1].get_uid()],
            "sources": [source],
            "actors": [{"uid": users[1].get_uid(), "sources": [source]}],
        }
        db.exec(update(m.DecisionCase).values(actor_snapshot=snapshot))
    with engine.begin() as connection:
        connection.execute(delete(m.BoardTeamMember).where(m.BoardTeamMember.team_id == team.id))
    with pytest.raises(DecisionGateBlocked, match="proposal_pending"):
        move(gate_native)
    assert current(gate_native).project_column_id == old.id


def test_nonblocking_authority_fence_and_ungated_parity(gate_native):
    from time import monotonic

    native, _, old, _ = gate_native
    engine = native[0]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL table lock contention contract")
    bind(gate_native, create(native))
    with engine.connect() as blocker:
        with blocker.begin():
            blocker.execute(text('LOCK TABLE "user" IN SHARE ROW EXCLUSIVE MODE'))
            started = monotonic()
            with pytest.raises(DecisionGateBlocked, match="authority_busy"):
                move(gate_native)
            assert monotonic() - started < 2
    assert current(gate_native).project_column_id == old.id
    _, gate, _, target = gate_native
    _, _, users, board, _, card = native
    gate.remove(users[0], board.get_uid(), card.get_uid(), target.workflow_stage)
    with engine.connect() as blocker:
        with blocker.begin():
            blocker.execute(text('LOCK TABLE "user" IN SHARE ROW EXCLUSIVE MODE'))
            assert move(gate_native) is True


def test_cross_board_move_cannot_bypass_gate(gate_native, monkeypatch):
    from uuid import uuid4
    from langboard_shared.domain.services import CardMoveOperations
    from langboard_shared.Env import Env

    native, _, old, target = gate_native
    _, flow, users, board, destination, card = native
    bind(gate_native, create(native))
    with DbSession.atomic() as db:
        column = m.ProjectColumn(project_id=destination.id, name="Done", order=0, workflow_stage=target.workflow_stage)
        db.insert(column)
        db.insert(m.ProjectAssignedUser(project_id=destination.id, user_id=users[0].id))
        db.insert(m.ProjectRole(project_id=destination.id, user_id=users[0].id, actions=["read", "card_write"]))

    @contextmanager
    def execution():
        with DbSession.atomic() as db:
            yield SimpleNamespace(
                db=db, watch_card_and_dependents=lambda *_: pytest.fail("Gate must precede relocation")
            )

    monkeypatch.setattr(CardMoveOperations, "execution_readiness_uow", execution)
    monkeypatch.setattr(type(Env), "COMMON_SECRET_KEY", property(lambda _: "gate-test-secret"))
    with pytest.raises(DecisionGateBlocked, match="proposal_pending"):
        flow.service.card.move_cross_board_card(users[0], board, card, destination, column, request_uid=str(uuid4()))
    assert current(gate_native).project_column_id == old.id
    assert current(gate_native).project_id == board.id


def test_gate_schema_upgrade_roundtrip(base_native):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    native = base_native
    engine, _, _, _, _, card = native
    case = create(native)
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024110000-7fab890c1234.py"
    spec = importlib.util.spec_from_file_location("gate_roundtrip", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    with DbSession.atomic() as db:
        decision = db.exec(SqlBuilder.select.table(m.DecisionCase)).first()
        db.insert(
            m.CardDecisionGate(
                card_id=card.id,
                target_stage="done",
                decision_id=decision.id,
                round_id=case["round_id"],
                target_revision=case["target_revision"],
                policy_revision=case["policy_revision"],
            )
        )
    with pytest.raises(RuntimeError, match="retained"):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
    with engine.begin() as connection:
        connection.execute(m.CardDecisionGate.__table__.delete())
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
    assert "card_decision_gate" not in inspect(engine).get_table_names()


def test_column_delete_preflights_every_gate_before_bulk_mutation(gate_native, monkeypatch):
    native, _, old, target = gate_native
    engine, flow, users, board, _, card = native
    bind(gate_native, create(native))
    with DbSession.atomic() as db:
        db.exec(update(m.ProjectColumn).where(m.ProjectColumn.id == target.id).values(is_archive=True))
        sibling = m.Card(project_id=board.id, project_column_id=old.id, title="Bulk sibling")
        db.insert(sibling)
    service = flow.service.project_column

    def forbidden(*args, **kwargs):
        pytest.fail("Bulk mutation or effects occurred before all gates passed")

    monkeypatch.setattr(service.repo.card, "move_all_by_column", forbidden)
    module = importlib.import_module(service.__module__)
    monkeypatch.setattr(module.ProjectColumnPublisher, "deleted", forbidden)
    monkeypatch.setattr(module.BotScopeHelper, "delete_by_scope", forbidden)
    with pytest.raises(DecisionGateBlocked, match="proposal_pending"):
        service.delete(users[0], board, old)
    with DbSession.atomic() as db:
        source = db.exec(SqlBuilder.select.table(m.ProjectColumn).where(m.ProjectColumn.id == old.id)).first()
        assert source is not None and source.deleted_at is None
        rows = db.exec(select(m.Card.project_column_id).where(m.Card.id.in_([card.id, sibling.id]))).all()
        assert all((row[0] if isinstance(row, tuple) else row) == old.id for row in rows)


def test_column_delete_rest_mcp_mapping(gate_native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.mcp_tools import ProjectMcp
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardColumnApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    native, _, old, target = gate_native
    _, flow, users, board, _, _ = native
    bind(gate_native, create(native))
    with DbSession.atomic() as db:
        db.exec(update(m.ProjectColumn).where(m.ProjectColumn.id == target.id).values(is_archive=True))
        db.exec(
            update(m.ProjectRole)
            .where(m.ProjectRole.user_id == users[0].id)
            .values(actions=["read", "update", "card_update"])
        )
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda _: "decision-gate-column-native-key-at-least-32-bytes")
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        access, refresh = AuthSecurity.authenticate(users[0].id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        result = client.delete(
            f"/board/{board.get_uid()}/column/{old.get_uid()}", headers={"Authorization": f"Bearer {access}"}
        )
        assert result.status_code == 409, result.text
        assert result.json()["code"] == "decision_gate_blocked"
        operation = ProjectMcp.delete_column
        while hasattr(operation, "__wrapped__"):
            operation = operation.__wrapped__
        assert operation(board.get_uid(), old.get_uid(), users[0], flow.service) == result.json()
    assert current(gate_native).project_column_id == old.id


@pytest.mark.parametrize("enclosing_transaction", [False, True])
def test_failed_dock_delete_rolls_back_bulk_scope_and_callbacks(gate_native, monkeypatch, enclosing_transaction):
    from contextlib import nullcontext

    native, _, old, target = gate_native
    _, flow, users, board, _, card = native
    case = create(native)
    bind(gate_native, case)
    respond(native, case)
    with DbSession.atomic() as db:
        db.exec(update(m.ProjectColumn).where(m.ProjectColumn.id == target.id).values(is_archive=True))
        bot = m.Bot(
            name="Scope fixture",
            bot_uname="scope-fixture",
            platform="default",
            platform_running_type="default",
            app_api_token="test-only",
        )
        db.insert(bot)
        scope = m.ProjectColumnBotScope(project_column_id=old.id, bot_id=bot.id, conditions=[])
        db.insert(scope)
    service = flow.service.project_column
    module = importlib.import_module(service.__module__)
    monkeypatch.setattr(module.BotScheduleHelper, "unschedule_by_scope", lambda *_: None)
    monkeypatch.setattr(flow.service.graph_approval_request, "cancel_pending_by_scope", lambda *_args, **_kwargs: None)
    callbacks = []
    observed = []

    def failed_dock(*_):
        with DbSession.atomic() as db:
            moved = db.exec(select(m.Card.project_column_id).where(m.Card.id == card.id)).first()
            assert (moved[0] if isinstance(moved, tuple) else moved) == target.id
            assert (
                db.exec(select(m.ProjectColumnBotScope.id).where(m.ProjectColumnBotScope.id == scope.id)).first()
                is None
            )
            observed.extend(["bulk_moved", "scope_removed"])
            db.after_commit(lambda: callbacks.append("must_rollback"))
        return None

    monkeypatch.setattr(service.repo.project_column, "delete_with_dock_snapshot", failed_dock)
    with DbSession.atomic() if enclosing_transaction else nullcontext():
        if enclosing_transaction:
            with DbSession.atomic() as db:
                db.insert(m.Card(project_id=board.id, project_column_id=old.id, title="Outer transaction survives"))
                db.after_commit(lambda: callbacks.append("outer_survives"))
        assert service.delete(users[0], board, old) is False
    assert observed == ["bulk_moved", "scope_removed"]
    assert callbacks == (["outer_survives"] if enclosing_transaction else [])
    with DbSession.atomic() as db:
        current_card = db.exec(SqlBuilder.select.table(m.Card).where(m.Card.id == card.id)).first()
        assert current_card.project_column_id == old.id
        assert current_card.archived_at is None
        assert (
            db.exec(select(m.ProjectColumnBotScope.id).where(m.ProjectColumnBotScope.id == scope.id)).first()
            is not None
        )
        assert db.exec(SqlBuilder.select.table(m.ProjectColumn).where(m.ProjectColumn.id == old.id)).first() is not None
        outer = db.exec(select(m.Card.id).where(m.Card.title == "Outer transaction survives")).first()
        assert bool(outer) is enclosing_transaction
