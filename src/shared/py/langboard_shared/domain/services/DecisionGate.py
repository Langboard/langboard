"""Native stage guards. No Graph dispatch and no nested Decision commands."""

from contextlib import contextmanager
from types import SimpleNamespace
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ..models import Card, CardDecisionGate, DecisionCase, Project, User, WorkflowStageDefinition
from .DecisionFlow import DecisionConflict, DecisionDenied, DecisionFlow, DecisionMissing, _uid, card_target_revision


class DecisionGateBlocked(Exception):
    def __init__(self, reason, next_action="Obtain current proposal approval or configure a fresh round"):
        super().__init__(reason)
        self.reason = reason
        self.next_action = next_action

    def payload(self):
        return {"code": "decision_gate_blocked", "reason": self.reason, "next_action": self.next_action}


def fence_move_authority(db):
    """Fence absent ACL/source rows for configured moves; never wait in reverse order.

    Callers hold the native project row fence before checking binding absence.
    Native app policy writers acquire the advisory fence first. Decision actor
    writers acquire organization/user/project/source tables. NOWAIT covers callers
    already inside native transactions and the older concrete-actor lock order.
    A conflict aborts the entire move, including queued after-commit effects.
    # ponytail: coarse native authority fences; partition only with cooperating writers.
    """
    dialect = DbEngine.get_main_engine().dialect.name
    try:
        if dialect == "postgresql":
            if not db.exec(select(func.pg_try_advisory_xact_lock(7104389251162))).first()[0]:
                raise DecisionGateBlocked("authority_busy", "Retry the move with current card state")
            db.exec(
                text(
                    'LOCK TABLE organization, "user", setting_role, scim_group, scim_group_member, '
                    "project, project_role, project_assigned_user, board_team, board_team_member, "
                    "card, employee_membership_policy, user_identity_link, decision_case, "
                    "decision_option, decision_response, card_decision_gate "
                    "IN SHARE ROW EXCLUSIVE MODE NOWAIT"
                )
            )
        elif dialect == "sqlite":
            # Acquire the SQLite writer fence even when there are no bindings.
            db.exec(update(CardDecisionGate).where(CardDecisionGate.id == -1).values(id=CardDecisionGate.id))
        else:
            raise RuntimeError("Decision gates support SQLite and PostgreSQL")
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) in ("55P03", "57014", "40P01", "40001") or (
            dialect == "sqlite" and "locked" in str(exc.orig).lower()
        ):
            raise DecisionGateBlocked("authority_busy", "Retry the move with current card state") from exc
        raise


class DecisionGate:
    def __init__(self, service):
        self.service = service if hasattr(service, "card") else SimpleNamespace(card=service)
        self.flow = DecisionFlow(self.service)

    @staticmethod
    def _binding(db, card, stage):
        return db.exec(
            SqlBuilder.select.table(CardDecisionGate)
            .where(
                CardDecisionGate.card_id == card.id,
                CardDecisionGate.target_stage == stage,
            )
            .execution_options(populate_existing=True)
        ).first()

    @staticmethod
    def _summary(gate):
        return {
            "target_stage": gate.target_stage,
            "decision_uid": gate.decision_id.to_short_code(),
            "round_id": gate.round_id,
            "target_revision": gate.target_revision,
            "policy_revision": gate.policy_revision,
        }

    def read(self, actor, project_uid, card_uid, *, channel=CollaborationChannel.Api):
        with self.flow._transaction(read_only=True) as db:
            _, card = self.flow._authorized(db, actor, project_uid, card_uid, channel)
            rows = db.exec(SqlBuilder.select.table(CardDecisionGate).where(CardDecisionGate.card_id == card.id)).all()
            return {"gates": [self._summary(row) for row in rows]}

    @contextmanager
    def _write(self, project_uid):
        if DbSession.has_active_transaction():
            raise DecisionConflict("Gate commands require their own transaction boundary")
        with DbSession.atomic(sensitive=True) as db:
            # Same native board fence as every move. This makes an absent gate
            # stable through movement without imposing global fences on ungated cards.
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                db.exec(update(CardDecisionGate).where(CardDecisionGate.id == -1).values(id=CardDecisionGate.id))
            db.exec(SqlBuilder.select.table(Project).where(Project.id == _uid(project_uid)).with_for_update()).first()
            fence_move_authority(db)
            yield db

    def configure(self, actor, project_uid, card_uid, form, *, channel=CollaborationChannel.Api):
        with self._write(project_uid) as db:
            _, card = self.flow._authorized(db, actor, project_uid, card_uid, channel, manage=True)
            stage = db.exec(
                SqlBuilder.select.table(WorkflowStageDefinition).where(
                    WorkflowStageDefinition.key == form.target_stage,
                    WorkflowStageDefinition.is_active.is_(True),
                )
            ).first()
            if stage is None:
                raise ValueError("Active native workflow stage required")
            case = db.exec(
                SqlBuilder.select.table(DecisionCase).where(
                    DecisionCase.id == _uid(form.decision_uid),
                    DecisionCase.card_id == card.id,
                    DecisionCase.project_id == card.project_id,
                )
            ).first()
            if case is None:
                raise DecisionMissing()
            if case.source_kind != "proposal":
                raise ValueError("Only proposal decisions can authorize a stage gate")
            if (case.round_id, case.target_revision, case.policy_revision) != (
                form.round_id,
                form.target_revision,
                form.policy_revision,
            ) or card_target_revision(card) != case.target_revision:
                raise DecisionConflict("Current exact proposal round required")
            gate = self._binding(db, card, form.target_stage)
            values = dict(
                card_id=card.id,
                target_stage=form.target_stage,
                decision_id=case.id,
                round_id=case.round_id,
                target_revision=case.target_revision,
                policy_revision=case.policy_revision,
            )
            if gate is None:
                gate = CardDecisionGate(**values)
                db.insert(gate)
            else:
                for key, value in values.items():
                    setattr(gate, key, value)
                db.update(gate)
            return self._summary(gate)

    def remove(self, actor, project_uid, card_uid, stage, *, channel=CollaborationChannel.Api):
        with self._write(project_uid) as db:
            _, card = self.flow._authorized(db, actor, project_uid, card_uid, channel, manage=True)
            gate = self._binding(db, card, stage)
            if gate:
                db.exec(SqlBuilder.delete.table(CardDecisionGate).where(CardDecisionGate.id == gate.id))
            return {"removed": gate is not None}

    def require(self, db, actor, project, card, old_column, new_column, *, channel=CollaborationChannel.Api):
        if (
            not new_column
            or not new_column.workflow_stage
            or (old_column and old_column.workflow_stage == new_column.workflow_stage)
        ):
            return
        if DbEngine.get_main_engine().dialect.name == "sqlite":
            # FOR UPDATE has no effect on SQLite. Reserve its writer before the
            # absence read, so configure cannot insert between guard and mutation.
            db.exec(text("UPDATE project SET id=id WHERE id=:project_id"), params={"project_id": int(project.id)})
        gate = self._binding(db, card, new_column.workflow_stage)
        if gate is None:
            return
        fence_move_authority(db)
        # Refresh the authoritative pre-move row, including timestamp-only writers.
        card = db.exec(
            SqlBuilder.select.table(Card).where(Card.id == card.id).execution_options(populate_existing=True)
        ).first()
        case = db.exec(
            SqlBuilder.select.table(DecisionCase)
            .where(
                DecisionCase.id == gate.decision_id,
            )
            .execution_options(populate_existing=True)
        ).first()
        if (
            case is None
            or case.card_id != card.id
            or case.project_id != card.project_id
            or case.source_kind != "proposal"
        ):
            raise DecisionGateBlocked("decision_unavailable")
        if (case.round_id, case.policy_revision, case.target_revision) != (
            gate.round_id,
            gate.policy_revision,
            gate.target_revision,
        ):
            raise DecisionGateBlocked("round_changed")
        if card_target_revision(card) != gate.target_revision:
            raise DecisionGateBlocked("target_revision_changed")
        # Human movement authority remains native. Bots/profiles retain their native
        # move checks; they obtain no response authority through this binding.
        if isinstance(actor, User):
            try:
                self.flow._authorized(db, actor, project.get_uid(), card.get_uid(), channel, manage=True)
            except (DecisionDenied, DecisionMissing):
                raise DecisionGateBlocked("move_authority_changed") from None
        evaluation = self.flow._evaluation(db, case, card, channel, SafeDateTime.now())
        if evaluation.state != "passed":
            raise DecisionGateBlocked("proposal_" + evaluation.state)


@contextmanager
def guard_column_delete_move(
    service, actor, project, source_column, archive_column, *, channel=CollaborationChannel.Api
):
    """Preflight every card before a stage-bearing legacy archive bulk move.

    Native archive columns cannot acquire stage bindings through the workflow API.
    Preserve the existing no-stage deletion path; persisted stage-bearing archive
    rows still require exactly the same native gate checks as individual moves.
    """
    if not getattr(archive_column, "workflow_stage", None):
        yield
        return
    from ..models import ProjectColumn
    from .factory.CardService import CardService

    # atomic() reuses an enclosing transaction; isolate this bulk operation
    # so a native False result cannot commit its partial mutations or callbacks.
    with DbSession.atomic() as db, db.savepoint():
        if DbEngine.get_main_engine().dialect.name == "sqlite":
            db.exec(text("UPDATE project SET id=id WHERE id=:project_id"), params={"project_id": int(project.id)})
        db.exec(SqlBuilder.select.table(Project).where(Project.id == project.id).with_for_update()).first()
        columns = {
            row.id: row
            for row in db.exec(
                SqlBuilder.select.table(ProjectColumn)
                .where(
                    ProjectColumn.id.in_([source_column.id, archive_column.id]), ProjectColumn.project_id == project.id
                )
                .order_by(ProjectColumn.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }
        source = columns.get(source_column.id)
        target = columns.get(archive_column.id)
        if source is None or target is None or source.deleted_at or target.deleted_at or not target.is_archive:
            raise DecisionGateBlocked("move_target_changed", "Read the current board columns and retry")
        cards = db.exec(
            SqlBuilder.select.table(Card)
            .where(
                Card.project_id == project.id,
                Card.project_column_id == source.id,
            )
            .order_by(Card.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        gate = DecisionGate(service._get_service(CardService))
        for card in cards:
            gate.require(db, actor, project, card, source, target, channel=channel)
        yield
