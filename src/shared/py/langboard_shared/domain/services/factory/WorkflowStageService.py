import base64
import re
import struct
from collections.abc import Mapping
from sqlalchemy import and_, func, or_, select
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.types import SafeDateTime
from ....helpers import InfraHelper
from ....publishers import AppSettingPublisher
from ....tasks.webhooks.ExecutionReadinessUow import execution_readiness_uow
from ...models import (
    AppConnection,
    AppDefinition,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from ...models.ProjectRole import ProjectRoleAction
from ..AppGovernance import AppGovernanceDenied, connection_discovery_scope, require_current_project_policy
from ..AppManifest import APP_MANIFESTS
from ..AppRegistry import approved_manifests
from ..AppSignalProjection import authorized_signal_rows, signal_resource_conditions
from ..AppWorkflowPolicy import (
    WorkflowMappingResult,
    WorkflowRequirements,
    resolve_app_workflow,
)
from ..CardVisibilityPolicy import CollaborationChannel, card_visibility_scope_by_project
from ..WorkflowCompactionHistory import history_lease_expired
from .CardService import CardService


class WorkflowStageEditConflict(Exception):
    """The editor's snapshot no longer matches the locked registry row."""


class WorkflowStageService(BaseDomainService):
    @staticmethod
    def name() -> str:
        return "workflow_stage"

    def preview_app_mapping(
        self, user: User, project: Project | int | str,
        requirements: WorkflowRequirements, explicit: Mapping[str, str],
    ) -> WorkflowMappingResult | None:
        """Preview current host facts, never execution or mutation authority.

        Re-read the primary so stale caller models, roles and replicas cannot
        authorize a board. App binding writes still require their own update gate.
        """
        return self._resolve_app_mapping(user, project, requirements, explicit, ProjectRoleAction.Read)

    def get_app_catalog(self, user: User, project_uid: str) -> list[dict] | None:
        """Host catalog and board-owned status, never installation authority."""
        with DbSession.atomic() as db:
            board = self._authorized_app_board(user, project_uid, ProjectRoleAction.Read, revocation=True)
            if board is None:
                return None
            bindings = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == InfraHelper.convert_id(project_uid),
            )).all()
            by_app = {binding.app_key: binding for binding in bindings}
            resource_counts = db.exec(select(
                AppResourceBinding.board_binding_id, AppResourceBinding.access_state,
                AppResourceBinding.health, AppConnection.state, func.count(AppResourceBinding.id),
            ).join(AppConnection, AppConnection.id == AppResourceBinding.connection_id)
              .join(BoardAppBinding, BoardAppBinding.id == AppResourceBinding.board_binding_id)
              .where(BoardAppBinding.project_id == InfraHelper.convert_id(project_uid),
                     AppResourceBinding.is_selected == True,  # noqa: E712
                     or_(
                         and_(AppConnection.ownership == "personal", AppConnection.owner_id == user.id),
                         and_(AppConnection.ownership == "organization", board.organization_id is not None,
                              AppConnection.organization_id == board.organization_id),
                     ))
              .group_by(AppResourceBinding.board_binding_id, AppResourceBinding.access_state,
                        AppResourceBinding.health, AppConnection.state)).all()
            summaries = {}
            for binding_id, access, health, connection, count in resource_counts:
                summary = summaries.setdefault(binding_id, {
                    "selected_count": 0, "access_counts": {}, "health_counts": {}, "connection_counts": {},
                })
                summary["selected_count"] += count
                for field, state in (("access_counts", access), ("health_counts", health), ("connection_counts", connection)):
                    summary[field][state] = summary[field].get(state, 0) + count

            definitions = {row.key: row for row in db.exec(SqlBuilder.select.table(AppDefinition)).all()}
            try:
                require_current_project_policy(db, board)
                policy_available = True
            except AppGovernanceDenied:
                policy_available = False
            items = []
            for key, manifest in approved_manifests(retained_keys=by_app).items():
                binding = by_app.get(key)
                available = policy_available and (key not in definitions or definitions[key].is_enabled)
                items.append({
                    **manifest.catalog_fields(),
                    "is_available": available,
                    "inbound_connection_management": key in definitions and key not in APP_MANIFESTS,
                    "capabilities": list(manifest.capabilities) if available else [],
                    "app_revision": definitions[key].edit_revision() if key in definitions else None,
                    "installation": definitions[key].registry_response()["installation"] if key in definitions else None,
                    "signal_sources": list(definitions[key].declaration.get("signal_sources", [])) if key in definitions else [],
                    "card_lifecycle": definitions[key].declaration.get("card_lifecycle", {}) if key in definitions else {},
                    "resources": summaries.get(binding.id if binding else None, {
                        "selected_count": 0, "access_counts": {}, "health_counts": {}, "connection_counts": {},
                    }),
                    "binding": None if binding is None else {
                        "uid": binding.get_uid(), "state": binding.state, "revision": binding.edit_revision(),
                        "granted_capabilities": list(binding.granted_capabilities),
                        "stage_transitions_enabled": binding.stage_transitions_enabled,
                        "archive_sync_types": list(binding.archive_sync_types),
                    },
                })
            return items

    def get_connection_context(self, user: User, project_uid: str, after: str | None = None, *, app_key: str | None = None) -> dict | None:
        """Current approved resource facts only; no credentials, provider I/O or cached authority."""
        after_id = None
        if after is not None:
            try:
                if len(after) != 23 or not re.fullmatch(r"[A-Za-z0-9_-]+", after):
                    raise ValueError()
                version, board_id, after_id = struct.unpack(">BQQ", base64.urlsafe_b64decode(after + "="))
                if version != 1 or board_id != InfraHelper.convert_id(project_uid):
                    raise ValueError()
            except (ValueError, TypeError, struct.error) as exc:
                raise ValueError("Invalid connection context cursor") from exc
        with DbSession.use(readonly=False) as db:
            board = self._authorized_app_board(user, project_uid, ProjectRoleAction.Read)
            if board is None:
                return None
            manifests = approved_manifests()
            scopes = [
                and_(*signal_resource_conditions(app_key=key, resource_type=kind, capability="resources.read"))
                for key, manifest in manifests.items()
                if "resources.read" in manifest.capabilities
                for kind in manifest.resource_types
            ]
            statement = (
                select(AppResourceBinding, AppConnection, BoardAppBinding)
                .join(BoardAppBinding, BoardAppBinding.id == AppResourceBinding.board_binding_id)
                .join(Project, Project.id == BoardAppBinding.project_id)
                .join(AppConnection, AppConnection.id == AppResourceBinding.connection_id)
                .join(User, User.id == AppConnection.owner_id)
                .where(Project.id == board.id, connection_discovery_scope(user.id), or_(False, *scopes))
            )
            if after_id is not None:
                statement = statement.where(AppResourceBinding.id > after_id)
            if app_key is not None:
                statement = statement.where(BoardAppBinding.app_key == app_key)
            rows = db.exec(statement.order_by(AppResourceBinding.id).limit(26)).all()
            # Native adapters require current upstream secret authority. Registered
            # inbound apps have no upstream secret and use explicit host connection
            # identity, declaration and board consent instead.
            native_rows = [row for row in rows[:25] if row[2].app_key in APP_MANIFESTS]
            allowed = {row.id for row in authorized_signal_rows(db, native_rows)}
            allowed.update(resource.id for resource, _, binding in rows[:25] if binding.app_key not in APP_MANIFESTS)
            return {
                "items": [
                    {
                        "resource_uid": resource.get_uid(),
                        "connection_uid": connection.get_uid(),
                        "binding_uid": binding.get_uid(),
                        "app_key": binding.app_key,
                        "resource_type": resource.resource_type,
                        "external_resource_id": resource.external_resource_id[:200],
                        "external_resource_id_truncated": len(resource.external_resource_id) > 200,
                        "name": str(
                            resource.resource_path[-1].get("name", resource.external_resource_id)
                            if resource.resource_path
                            else resource.external_resource_id
                        )[:200],
                        "access_revision": resource.access_revision,
                        "binding_revision": binding.edit_revision(),
                        "health": resource.health,
                    }
                    for resource, connection, binding in rows[:25]
                    if resource.id in allowed
                ],
                "next_cursor": (
                    base64.urlsafe_b64encode(struct.pack(">BQQ", 1, int(board.id), int(rows[24][0].id)))
                    .decode().rstrip("=") if len(rows) > 25 else None
                ),
                "limit": 25,
            }

    def disable_app_binding(
        self, user: User, project_uid: str, app_key: str, binding_uid: str, expected_revision: str,
    ) -> BoardAppBinding | None:
        """Disable this board configuration without deleting shared connections or resources."""
        with DbSession.atomic() as db:
            board = db.exec(SqlBuilder.select.table(Project).where(
                Project.id == InfraHelper.convert_id(project_uid),
            ).with_for_update()).first()
            if board is None:
                return None
            if self._authorized_app_board(user, board.id, ProjectRoleAction.Update, lock=True, revocation=True) is None:
                return None
            binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.id == InfraHelper.convert_id(binding_uid),
                BoardAppBinding.project_id == board.id, BoardAppBinding.app_key == app_key,
            ).with_for_update()).first()
            if binding is None:
                return None
            if binding.edit_revision() != expected_revision:
                raise WorkflowStageEditConflict()
            self._disable_locked_app_binding(db, binding)
            db.after_commit(AppSettingPublisher.apps_changed)
            return binding

    @staticmethod
    def _disable_locked_app_binding(db, binding):
        """Shared withdrawal semantics; caller owns authorization and row locks."""
        binding.state = "disabled"
        binding.stage_transitions_enabled = False
        binding.granted_capabilities = []
        if binding.archive_sync_types:
            binding.archive_sync_types = []
            binding.archive_sync_revision += 1
        db.update(binding)

    def get_app_mapping(self, user: User, project_uid: str, app_key: str) -> dict | None:
        manifest = approved_manifests().get(app_key)
        requirements = manifest.workflow_requirements if manifest else None
        if requirements is None:
            return None
        with DbSession.atomic() as db:
            binding = db.exec(
                SqlBuilder.select.table(BoardAppBinding).where(
                    BoardAppBinding.project_id == InfraHelper.convert_id(project_uid),
                    BoardAppBinding.app_key == app_key,
                )
            ).first()
            result = self.preview_app_mapping(
                user,
                project_uid,
                requirements,
                binding.workflow_mapping if binding else {},
            )
            if result is None:
                return None
            columns = db.exec(
                SqlBuilder.select.table(ProjectColumn).where(
                    ProjectColumn.project_id == InfraHelper.convert_id(project_uid),
                    ProjectColumn.deleted_at.is_(None),
                    ProjectColumn.is_archive == False,  # noqa: E712
                )
            ).all()
            stage_keys = set(requirements.required + requirements.optional)
            stage_keys.update(column.workflow_stage for column in columns if column.workflow_stage)
            stages = db.exec(
                SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key.in_(stage_keys))
            ).all()
            return {
                "binding": binding,
                "mapping": result,
                "workflow_stages": {stage.key: stage.api_response() for stage in stages},
                "column_names": {column.get_uid(): column.name for column in columns},
                "available_columns": [
                    {"uid": column.get_uid(), "name": column.name, "workflow_stage": column.workflow_stage}
                    for column in columns
                ],
            }

    def prepare_app_mapping(self, user: User, project_uid: str, app_key: str) -> BoardAppBinding | None:
        """Create only a disabled workflow draft; this is not App installation."""
        manifest = approved_manifests().get(app_key)
        requirements = manifest.workflow_requirements if manifest else None
        if requirements is None:
            return None
        with DbSession.atomic() as db:
            project_id = InfraHelper.convert_id(project_uid)
            board = db.exec(SqlBuilder.select.table(Project).where(
                Project.id == project_id,
            ).with_for_update()).first()
            if board is None:
                return None
            binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == app_key,
            ).with_for_update()).first()
            result = self._resolve_app_mapping(
                user, project_id, requirements, {}, ProjectRoleAction.Update, lock=True,
            )
            if result is None:
                return None
            if binding is not None:
                return binding
            binding = BoardAppBinding(
                project_id=project_id, app_key=app_key,
                workflow_mapping={choice.stage: choice.column_uid for choice in result.choices if choice.status == "resolved"},
            )
            db.insert(binding)
            return binding

    def save_app_mapping(
        self, user: User, binding_uid: str,
        explicit: Mapping[str, str] | None, *, project_uid: str, app_key: str, expected_revision: str,
        enable_transitions: bool,
    ) -> BoardAppBinding | None:
        """Resolve requirements from the saved App key, never from request bodies.

        Persist current choices without changing resource selections, grants or
        App activation. Incomplete mappings may be saved with transitions off.
        This does not authorize future transition execution.
        """
        with DbSession.atomic() as db:
            # Match draft creation's lock order: board before binding.
            board = db.exec(SqlBuilder.select.table(Project).where(
                Project.id == InfraHelper.convert_id(project_uid),
            ).with_for_update()).first()
            if board is None:
                return None
            binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.id == InfraHelper.convert_id(binding_uid),
                BoardAppBinding.project_id == InfraHelper.convert_id(project_uid),
                BoardAppBinding.app_key == app_key,
            ).with_for_update()).first()
            if binding is None:
                return None
            manifest = approved_manifests().get(binding.app_key)
            requirements = manifest.workflow_requirements if manifest else None
            if requirements is None:
                return None
            mapping = dict(binding.workflow_mapping if explicit is None else explicit)
            result = self._resolve_app_mapping(
                user, binding.project_id, requirements, mapping, ProjectRoleAction.Update, lock=True,
            )
            if result is None:
                return None
            if binding.edit_revision() != expected_revision:
                raise WorkflowStageEditConflict()
            if enable_transitions and not result.transitions_enabled:
                raise ValueError("App workflow mapping is incomplete or invalid")
            # Save automatic unique choices too, so a later duplicate cannot
            # silently change an existing App destination.
            binding.workflow_mapping = {
                **mapping,
                **{choice.stage: choice.column_uid for choice in result.choices if choice.status == "resolved"},
            }
            binding.stage_transitions_enabled = enable_transitions
            db.update(binding)
            return binding

    def _authorized_app_board(self, user: User, project: Project | int | str,
                              action: ProjectRoleAction, *, lock: bool = False, revocation: bool = False) -> Project | None:
        def query(model):
            statement = SqlBuilder.select.table(model)
            return statement.with_for_update() if lock else statement

        if not isinstance(user, User):
            return None
        project_id = InfraHelper.convert_id(project)
        with DbSession.use(readonly=False) as db:
            current = db.exec(query(User).where(User.id == user.id)).first()
            board = db.exec(query(Project).where(Project.id == project_id)).first()
            if current is None or current.deleted_at or not current.activated_at or board is None or board.deleted_at:
                return None
            if not current.is_admin and board.owner_id != current.id:
                member = db.exec(query(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == board.id, ProjectAssignedUser.user_id == current.id,
                )).first()
                role = db.exec(query(ProjectRole).where(
                    ProjectRole.project_id == board.id, ProjectRole.user_id == current.id,
                )).first()
                if member is None or role is None or not role.is_granted(action):
                    return None
            # Removing authority must remain available after policy or registry revocation.
            if not revocation:
                try:
                    require_current_project_policy(db, board)
                except AppGovernanceDenied:
                    return None
            return board

    def _resolve_app_mapping(
        self, user: User, project: Project | int | str,
        requirements: WorkflowRequirements, explicit: Mapping[str, str],
        action: ProjectRoleAction, *, lock: bool = False,
    ) -> WorkflowMappingResult | None:
        def query(model):
            statement = SqlBuilder.select.table(model)
            return statement.with_for_update() if lock else statement

        with DbSession.use(readonly=False) as db:
            board = self._authorized_app_board(user, project, action, lock=lock)
            if board is None:
                return None
            columns = db.exec(query(ProjectColumn).where(ProjectColumn.project_id == board.id)).all()
            keys = requirements.required + requirements.optional
            stages = db.exec(query(WorkflowStageDefinition).where(
                WorkflowStageDefinition.key.in_(keys),
            )).all()
        return resolve_app_workflow(
            requirements, int(board.id), columns, stages, explicit,
            authorized_column_ids=frozenset(int(column.id) for column in columns),
        )

    def get_api_list(self) -> list[dict]:
        usage = self.repo.workflow_stage.get_column_usage()
        return [
            {**stage.api_response(), "used_column_count": usage.get(stage.key, 0)}
            for stage in sorted(InfraHelper.get_all(WorkflowStageDefinition), key=lambda s: (s.order, s.key))
        ]

    def get_bot_options(self, user: User) -> dict:
        """Minimal Bot identities and schedules on cards visible to this administrator."""
        from ....helpers import BotHelper
        from ...constants.DomainEventType import DomainEventType
        from ...models import Bot, BotSchedule, CardBotSchedule
        from ...models.BotSchedule import BotScheduleRunningType, BotScheduleStatus

        contexts = self._get_service(CardService).resolve_work_visibility_contexts(user, CollaborationChannel.Api)
        entry_bots = set()
        policy_bots = set()
        from datetime import timezone

        def utc(value):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

        now = utc(SafeDateTime.now())
        with DbSession.use(readonly=False) as db:
            card_conditions = {condition for condition in DomainEventType if condition.value.startswith("card_")}
            models = set().union(*(BotHelper.get_scope_model_classes_by_condition(condition) for condition in card_conditions))
            for model in models:
                default_model = BotHelper.get_default_scope_model_class(model.get_scope_column_name())
                defaults = {row.bot_default_scope_branch_id: row.conditions for row in db.exec(SqlBuilder.select.table(default_model)).all()} if default_model else {}
                for scope in db.exec(SqlBuilder.select.table(model).where(model.is_frozen.is_(False))).all():
                    conditions = defaults.get(scope.default_scope_branch_id, []) if scope.default_scope_branch_id else scope.conditions
                    if any(condition in conditions for condition in card_conditions):
                        policy_bots.add(scope.bot_id)
                    if DomainEventType.CardMoved in conditions:
                        entry_bots.add(scope.bot_id)
            bots = db.exec(SqlBuilder.select.table(Bot)).all()
            rows = db.exec(SqlBuilder.select.tables(BotSchedule, Card)
                .join(CardBotSchedule, CardBotSchedule.bot_schedule_id == BotSchedule.id)
                .join(Card, Card.id == CardBotSchedule.card_id)
                .join(Project, Project.id == Card.project_id)
                .where(Card.deleted_at.is_(None), Card.archived_at.is_(None), Project.deleted_at.is_(None),
                       Card.visibility.in_(["INTERNAL", "SHARED"]), Card.source_type.is_(None),
                       card_visibility_scope_by_project(contexts))).all()
        return {
            "bots": [{"uid": bot.get_uid(), "name": bot.name, "entry_available": bot.id in entry_bots,
                      "policy_available": bot.id in policy_bots} for bot in bots],
            "schedules": [{"uid": schedule.get_uid(), "bot_uid": InfraHelper.convert_uid(schedule.bot_id),
                           "card_uid": card.get_uid(), "name": card.title,
                           "is_available": schedule.status == BotScheduleStatus.Started
                           and (schedule.running_type == BotScheduleRunningType.Infinite or
                                (schedule.running_type == BotScheduleRunningType.Duration and schedule.start_at is not None
                                 and schedule.end_at is not None and utc(schedule.start_at) <= now < utc(schedule.end_at)))}
                          for schedule, card in rows],
        }

    def get_effect_history(self, user: User, stage_uid: str, *, before: str | None = None, limit: int = 20,
                           channel: CollaborationChannel = CollaborationChannel.Api) -> dict | None:
        """Apply current card audiences in SQL before paging committed effect receipts."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Workflow history limit must be between 1 and 50")
        if not isinstance(user, User):
            return None
        with DbSession.atomic() as db:
            stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
                WorkflowStageDefinition.id == InfraHelper.convert_id(stage_uid),
            ).execution_options(populate_existing=True)).first()
            current = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)
                              .execution_options(populate_existing=True)).first()
            if stage is None or current is None or current.deleted_at is not None or not current.activated_at:
                return None
            contexts = self._get_service(CardService).resolve_work_visibility_contexts(current, channel)
            from ...models import WorkflowActionRun, WorkflowRuleRun
            from .WorkflowRuleService import rows as rule_rows
            from .WorkflowRuleService import utc

            def authorized_query(model):
                return (SqlBuilder.select.table(model)
                    .join(Card, and_(Card.id == model.card_id, Card.project_id == model.project_id))
                    .join(Project, Project.id == Card.project_id)
                    .where(model.stage_id == stage.id, Card.deleted_at.is_(None),
                           Project.deleted_at.is_(None), card_visibility_scope_by_project(contexts)))

            models = (WorkflowRuleRun, WorkflowEffectRun)
            queries = {model: authorized_query(model) for model in models}
            if before is not None:
                anchor_id = InfraHelper.convert_id(before)
                if not any(db.exec(query.where(model.id == anchor_id)).first() for model, query in queries.items()):
                    raise ValueError("Workflow history cursor is unavailable")
                queries = {model: query.where(model.id < anchor_id) for model, query in queries.items()}
            merged = sorted([row for model, query in queries.items()
                for row in db.exec(query.order_by(model.id.desc()).limit(limit + 1)).all()], key=lambda row: row.id, reverse=True)
            items, legacy_items = [], []
            now = SafeDateTime.now()
            for row in merged[:limit]:
                common = {"uid": row.get_uid(), "status": row.status,
                    "project_uid": InfraHelper.convert_uid(row.project_id), "card_uid": InfraHelper.convert_uid(row.card_id),
                    "actor_uid": InfraHelper.convert_uid(row.actor_id) if row.actor_id is not None else None,
                    "actor_type": row.actor_type, "stage_revision": row.stage_revision}
                if isinstance(row, WorkflowEffectRun):
                    legacy_items.append({**common, "source": row.source,
                        "status": "uncertain" if history_lease_expired(row, now) else row.status,
                        "effects": row.effects, "started_at": row.started_at, "finished_at": row.finished_at,
                        "duration_ms": row.duration_ms, "result": row.result,
                        "profile": {"uid": InfraHelper.convert_uid(row.profile_id), "display_name": row.profile_name} if row.profile_id is not None else None,
                        "error_code": "lease_expired" if history_lease_expired(row, now) else row.error_code})
                    continue
                actions = rule_rows(db, WorkflowActionRun, rule_run_id=row.id)
                items.append({**common, "rule_uid": InfraHelper.convert_uid(row.rule_id), "rule_revision": row.rule_revision,
                    "configured_by_uid": InfraHelper.convert_uid(row.configured_by_id) if row.configured_by_id is not None else None,
                    "event_id": row.event_id, "event_type": row.event_type, "occurred_at": row.occurred_at,
                    "residency": row.residency, "condition_results": row.condition_results, "rule_snapshot": row.rule_snapshot,
                    "error_code": row.error_code, "finished_at": row.finished_at,
                    "actions": [{"uid": a.get_uid(), "action_uid": InfraHelper.convert_uid(a.action_id), "kind": a.kind,
                        "executor_uid": InfraHelper.convert_uid(a.executor_id) if a.executor_id is not None else None,
                        "executor_type": a.executor_type, "status": "uncertain" if a.status == "running" and a.lease_expires_at and utc(a.lease_expires_at) <= utc(now) else a.status,
                        "attempt": a.attempt, "attempts": a.attempts, "idempotency_key": a.idempotency_key,
                        "result": a.result, "error_code": a.error_code, "started_at": a.started_at, "finished_at": a.finished_at,
                        "legacy_effect_run_uid": InfraHelper.convert_uid(a.legacy_effect_run_id) if a.legacy_effect_run_id is not None else None} for a in sorted(actions, key=lambda a: a.id)]})
            return {"items": items, "legacy_items": legacy_items,
                    "next_cursor": merged[limit - 1].get_uid() if len(merged) > limit else None}

    def get_api_by_keys(self, keys: set[str]) -> dict[str, dict]:
        """Resolve only workflow definitions used by an authorized result page."""
        return {key: stage.api_response() for key, stage in self.repo.workflow_stage.get_by_keys(keys).items()}

    def save(self, fields: dict, uid: str | None = None, *, actor: User | None = None) -> WorkflowStageDefinition | None:
        fields = dict(fields)
        expected_revision = fields.pop("expected_revision", None)
        if set(fields) - {
            "key",
            "name",
            "description",
            "color",
            "order",
            "counts_as_completed",
            "active_queue_policy",
            "overdue_policy",
            "rules",
            "confirm_descendant_move",
            "translations",
        }:
            raise ValueError("Unsupported workflow fields")
        stage = InfraHelper.get_by_id_like(WorkflowStageDefinition, uid) if uid else None
        if uid and not stage:
            return None
        from .WorkflowRuleService import replace_rules, validate_rules
        definitions = validate_rules(fields.pop("rules"))
        if type(fields.get("confirm_descendant_move")) is not bool:
            raise ValueError("Invalid descendant move policy")
        key = fields["key"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or (stage and stage.key != key):
            raise ValueError("Workflow key is invalid or immutable")
        name, description, color = fields["name"].strip(), fields["description"], fields["color"]
        if not name or len(name) > 100 or len(description) > 4000 or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("Invalid workflow display")
        if fields["active_queue_policy"] not in {"include", "exclude", "conditional"} or fields[
            "overdue_policy"
        ] not in {"normal", "suppress"}:
            raise ValueError("Invalid workflow policy")
        translations = {lang: dict(text) for lang, text in fields["translations"].items()}
        if len(set(translations) | {"en"}) > 30:
            raise ValueError("Too many workflow languages")
        for lang, text in translations.items():
            if (
                not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", lang)
                or set(text) - {"name", "description"}
                or len(text.get("name", "")) > 100
                or len(text.get("description", "")) > 4000
            ):
                raise ValueError("Invalid workflow translation")
        translations["en"] = {"name": name, "description": description}
        existing = InfraHelper.get_by(WorkflowStageDefinition, "key", key)
        if existing and (not stage or existing.id != stage.id):
            raise ValueError("Workflow key already exists")
        values = {**fields, "name": name, "color": color.upper(), "translations": translations}
        if stage:
            with DbSession.atomic() as db:
                stage = db.exec(
                    SqlBuilder.select.table(WorkflowStageDefinition)
                    .where(WorkflowStageDefinition.column("id") == stage.id)
                    .with_for_update().execution_options(populate_existing=True)
                ).first()
                if stage is None:
                    return None
                if expected_revision is not None and expected_revision != stage.edit_revision():
                    raise WorkflowStageEditConflict()
                policy_changed = any(
                    getattr(stage, field) != values[field]
                    for field in (
                        "counts_as_completed",
                        "active_queue_policy",
                        "overdue_policy",
                    )
                )
                if stage.counts_as_completed != values["counts_as_completed"]:
                    with execution_readiness_uow() as execution:
                        execution.watch_workflow_stage(stage.key)
                        for field, value in values.items():
                            setattr(stage, field, value)
                        self.repo.workflow_stage.update(stage)
                else:
                    for field, value in values.items():
                        setattr(stage, field, value)
                    self.repo.workflow_stage.update(stage)
                replace_rules(db, stage, definitions, configured_by_id=actor.id if actor else None)
                if policy_changed:
                    stage_key = stage.key
                    db.after_commit(lambda: self._publish_work_states(stage_key))
                db.after_commit(AppSettingPublisher.workflow_stages_changed)
        else:
            stage = WorkflowStageDefinition(**values)
            with DbSession.atomic() as db:
                self.repo.workflow_stage.insert(stage)
                replace_rules(db, stage, definitions, configured_by_id=actor.id if actor else None)
                db.after_commit(AppSettingPublisher.workflow_stages_changed)
        return stage

    def deactivate(self, uid: str, expected_revision: str | None = None) -> WorkflowStageDefinition | None:
        stage = InfraHelper.get_by_id_like(WorkflowStageDefinition, uid)
        if stage:
            with DbSession.atomic() as db:
                stage = db.exec(
                    SqlBuilder.select.table(WorkflowStageDefinition)
                    .where(WorkflowStageDefinition.column("id") == stage.id)
                    .with_for_update()
                ).first()
                if stage is None:
                    return None
                if expected_revision is not None and expected_revision != stage.edit_revision():
                    raise WorkflowStageEditConflict()
                stage.is_active = False
                self.repo.workflow_stage.update(stage)
                db.after_commit(AppSettingPublisher.workflow_stages_changed)
        return stage

    def _publish_work_states(self, key: str) -> None:
        """Refresh native card projections after commit; never replay entry effects."""
        grouped = {}
        for project_id, card_id in self.repo.workflow_stage.get_policy_affected_cards(key):
            grouped.setdefault(project_id, []).append(card_id)
        for project_id, card_ids in grouped.items():
            project = InfraHelper.get_by_id_like(Project, project_id)
            if project is not None:
                self._get_service(CardService).publish_work_states(project, card_ids)
