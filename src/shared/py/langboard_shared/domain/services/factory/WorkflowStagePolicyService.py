"""Stage residency and native atomic rule actions share the move transaction."""
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.types import SafeDateTime
from ...models import Card, Project, ProjectColumn, WorkflowRuleRun, WorkflowStageDefinition


class WorkflowStagePolicyService(BaseDomainService):
    @staticmethod
    def name() -> str:
        return "workflow_stage_policy"

    def apply_transition(self, actor, project: Project, card: Card, old_column: ProjectColumn,
                         new_column: ProjectColumn | None) -> dict[str, int]:
        empty = {"completed": 0, "stopped": 0}
        if new_column is None or card.is_linked_resource:
            return empty
        with DbSession.atomic() as db:
            if new_column.is_archive or not new_column.workflow_stage:
                card.workflow_stage_entered_key, card.workflow_stage_entered_at = None, None
                db.update(card)
                return empty
            if old_column.workflow_stage == new_column.workflow_stage:
                return empty
            stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
                WorkflowStageDefinition.key == new_column.workflow_stage)).first()
            if stage is None:
                raise ValueError("Workflow stage definition is missing")
            card.workflow_stage_entered_key, card.workflow_stage_entered_at = stage.key, SafeDateTime.now()
            db.update(card)
            from ....tasks.WorkflowRuleTask import execute_atomic_prefix
            from .. import DomainService
            from .WorkflowRuleService import CAUSE_RUN, admit, utc
            service = DomainService()
            ids = admit(db, actor, card, "stage_entered", event_id=f"entry:{stage.id}:{card.id}:{utc(card.workflow_stage_entered_at).isoformat()}",
                        context={"metadata": {"old_project_column_uid": old_column.get_uid()}})
            for run_id in ids:
                run = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == run_id)).first()
                token = CAUSE_RUN.set(run.id)
                try:
                    execute_atomic_prefix(db, run, service)
                finally:
                    CAUSE_RUN.reset(token)
            return empty
