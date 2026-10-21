from time import monotonic_ns
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.types import SafeDateTime
from ...models import Card, Project, ProjectColumn, User, WorkflowEffectRun, WorkflowStageDefinition
from .CheckitemService import CheckitemService
from .NotificationService import NotificationService


class WorkflowStagePolicyService(BaseDomainService):
    @staticmethod
    def name() -> str:
        return "workflow_stage_policy"

    def apply_transition(
        self, actor, project: Project, card: Card, old_column: ProjectColumn, new_column: ProjectColumn | None
    ) -> dict[str, int]:
        empty = {"completed": 0, "stopped": 0}
        if (
            new_column is None
            or card.is_linked_resource
        ):
            return empty
        with DbSession.atomic() as db:
            if new_column.is_archive or not new_column.workflow_stage:
                card.workflow_stage_entered_key = None
                card.workflow_stage_entered_at = None
                db.update(card)
                return empty
            if old_column.workflow_stage == new_column.workflow_stage:
                return empty
            stage = db.exec(
                SqlBuilder.select.table(WorkflowStageDefinition).where(
                    WorkflowStageDefinition.column("key") == new_column.workflow_stage
                ).execution_options(populate_existing=True)
            ).first()
            if stage is None:
                raise ValueError("Workflow stage definition is missing")
            # Inactive definitions keep existing bindings and their meaning.
            effects = set(stage.entry_effects)
            if effects - {"complete_checkitems", "stop_running_timers", "notify_author_on_entry", "confirm_descendant_move"}:
                raise ValueError("Unsupported native workflow effect")
            effects.discard("confirm_descendant_move")
            started_at = SafeDateTime.now()
            card.workflow_stage_entered_key = stage.key
            card.workflow_stage_entered_at = started_at
            db.update(card)
            if not effects:
                return empty
            started_clock = monotonic_ns()
            result = dict(empty)
            if effects & {"complete_checkitems", "stop_running_timers"}:
                result = self._get_service(CheckitemService).complete_unchecked_by_card(
                    actor,
                    project,
                    card,
                    complete="complete_checkitems" in effects,
                )
            if "notify_author_on_entry" in effects:
                notified = self._get_service(NotificationService).notify_workflow_stage_entered(
                    actor, project, card, new_column, stage.key,
                )
                result["notified"] = int(notified)
            db.insert(WorkflowEffectRun(
                stage_id=stage.id,
                stage_key=stage.key,
                stage_revision=stage.edit_revision(),
                project_id=project.id,
                card_id=card.id,
                actor_id=actor.id,
                actor_type="user" if isinstance(actor, User) else "bot",
                source="entry",
                effects=sorted(effects),
                started_at=started_at,
                finished_at=SafeDateTime.now(),
                duration_ms=max(0, (monotonic_ns() - started_clock) // 1_000_000),
                result=result,
            ))
            return result
