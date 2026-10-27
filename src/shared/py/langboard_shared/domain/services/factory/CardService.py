import base64
import json
import logging
from binascii import Error as Base64Error
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import monotonic_ns
from typing import Any, Literal, Sequence, cast, get_args, overload
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import Text, func, or_, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer, load_only
from ....ai import BotScheduleHelper, BotScopeHelper
from ....core.db import DbSession, EditorContentModel, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....core.domain.BaseDomainService import TMutableValidatorMap
from ....core.exceptions.CardDeleteForbidden import CardDeleteForbidden
from ....core.exceptions.CardDescriptionConflict import CardDescriptionConflict
from ....core.schema import TimeBasedPagination
from ....core.search.ElasticsearchKeywordSearch import search_document_candidates, search_document_candidates_many
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import (
    TCardParam,
    TColumnParam,
    TProjectLabelParam,
    TProjectParam,
    TUserOrBot,
    TWikiParam,
)
from ....core.utils.Converter import convert_python_data
from ....Env import Env
from ....helpers import InfraHelper
from ....publishers import CardPublisher
from ....tasks.activities import CardActivityTask
from ....tasks.bots import CardBotTask
from ....tasks.webhooks.ExecutionReadinessUow import execution_readiness_uow
from ...contracts.title_labels import global_label_names, parse_title_labels
from ...models import (
    Bot,
    Card,
    CardAppOwnership,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardBotSchedule,
    CardBotScope,
    CardChangeReceipt,
    CardRelationship,
    CardVerificationRecord,
    CardVisibilityChange,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    IdentityProvider,
    InternalBot,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    ProjectWiki,
    User,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from ...models.Checkitem import CheckitemStatus
from ...models.ProjectActivity import ProjectActivityType
from ...models.ProjectRole import ProjectRoleAction
from ..AppSignalProjection import card_signal_projections
from ..CardAppMutation import guard_card_app_mutation, require_card_app_mutation
from ..CardApprovalGate import pending_card_approvals
from ..CardChangeTransaction import card_change_snapshot, card_change_transaction, dispatch_after_commit
from ..CardDependencyState import card_dependency_state
from ..CardVerification import VerificationConflict, VerificationSubmission
from ..CardVisibilityPolicy import CardVisibility, CardVisibilityContext
from ..CardWorkState import project_work_state
from ..CompactorBoardAuthority import require_compactor_card
from ..DecisionGate import DecisionGate
from ..DependencyPolicy import dependency_blockers
from ..ExecutionGeneration import execution_generations
from .CardContentBlockService import CardContentBlockService
from .CardRelationshipService import CardRelationshipService
from .CheckitemService import CheckitemService
from .DoclingMetadataService import DoclingMetadataService
from .GlobalLabelService import GlobalLabelService
from .GraphApprovalRequestService import GraphApprovalRequestService
from .NotificationService import NotificationService
from .ProjectLabelService import ProjectLabelService
from .ProjectService import ProjectService
from .ProjectWikiService import ProjectWikiService
from .ScimProvisioningService import ScimProvisioningService
from .WorkflowStagePolicyService import WorkflowStagePolicyService


MyWorkGroup = Literal["overdue", "today", "upcoming", "review", "assigned", "mentioned", "created"]


class CardService(BaseDomainService):
    @staticmethod
    def _reject_private_assignment(card: Card) -> None:
        """Private cards are immutable solo ownership boundaries."""
        if getattr(card, "visibility", None) == CardVisibility.Private.value:
            raise PermissionError("Private cards do not support assignees")

    CONTEXT_DESCRIPTION_MAX_LENGTH = 1200
    LINKED_RESOURCE_PREVIEW_MAX_LENGTH = 240
    MIRROR_INVALIDATION_BATCH_SIZE = 200

    @staticmethod
    def _contains_relationship_type() -> GlobalCardRelationshipType:
        """Choose a system containment type instead of relying on query order."""

        relation_type = next(
            (
                candidate
                for candidate in InfraHelper.get_all(GlobalCardRelationshipType)
                if candidate.is_system_default and candidate.machine_semantic == "contains" and candidate.is_active
            ),
            None,
        )
        if relation_type is None:
            raise ValueError("System contains relationship type is missing")
        return relation_type

    UNREAD_TARGET_DESCRIPTION = "description"
    UNREAD_TARGET_COMMENT = "comment"
    UNREAD_TARGET_CARD = "card"

    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "card"

    @staticmethod
    def next_change_seq() -> int:
        """Draw the next monotonic cursor from the global change sequence."""

        with DbSession.use(readonly=False) as db:
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                # Called after a card write lock; SQLite serializes database writers.
                row = db.exec(select(func.coalesce(func.max(Card.last_change_seq), 0) + 1)).first()
            else:
                row = db.exec(select(func.nextval("content_change_seq"))).first()
            return int((row[0] if isinstance(row, tuple) else row) or 0)

    def mark_card_changed(
        self,
        card: Card,
        target_type: str,
        target_id: SnowflakeID | None = None,
    ) -> None:
        """Serialize native stamps and retain each committed change exactly once."""
        with DbSession.atomic() as db:
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                db.exec(update(Card.__table__).where(Card.id == card.id).values(last_change_seq=Card.last_change_seq))
            current = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.id == card.id)
                # Child INSERTs hold FK KEY SHARE locks. NO KEY UPDATE orders
                # stamps without deadlocking concurrent comment/attachment writes.
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            ).first()
            if current is None or current.deleted_at is not None:
                raise ValueError("Card no longer available for change receipt")
            if current.is_linked_resource:
                return
            boundary = current.change_count_since_seq
            if current.last_change_seq:
                previous = db.exec(
                    select(CardChangeReceipt.id).where(
                        CardChangeReceipt.card_id == current.id,
                        CardChangeReceipt.change_seq == current.last_change_seq,
                    )
                ).first()
                if previous is None:
                    # An old writer or imported stamp broke coverage. Never hide
                    # that gap merely because a new native receipt follows it.
                    boundary = max(boundary, current.last_change_seq)
            seq = self.next_change_seq()
            now = SafeDateTime.now()
            values = {
                "last_change_seq": seq,
                "last_change_target_type": target_type,
                "last_change_target_id": target_id,
                "last_change_at": now,
                "change_count_since_seq": boundary,
                "updated_at": now,
            }
            db.exec(update(Card.__table__).where(Card.id == current.id).values(**values))
            db.insert(CardChangeReceipt(card_id=current.id, change_seq=seq))
            for key, value in values.items():
                setattr(card, key, value)
            card.clear_changes()
            # Route invalidation to the current board even when the caller held
            # an object from before a same-identity cross-board move.
            changed_card = current.model_copy(update=values, deep=True)
            db.after_commit(lambda: CardPublisher.metadata_changed(changed_card))

    def get_card_read_state(
        self,
        project: TProjectParam,
        card: TCardParam,
        *,
        user: User,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> dict[str, Any] | None:
        resolved = self.resolve_readable_card(project, card, user, channel)
        if resolved is None:
            return None
        _, card, _ = resolved
        return {"card_uid": card.get_uid(), "readers": self.repo.user_card_read_state.get_readers(card)}

    def mark_card_seen(
        self,
        user: User,
        card: TCardParam | None,
        project: TProjectParam,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> dict[str, Any] | None:
        return self.set_card_read_state(user, project, card, True, channel=channel)

    def set_card_read_state(
        self,
        user: User,
        project: TProjectParam,
        card: TCardParam | None,
        seen: bool,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> dict[str, Any] | None:
        if not isinstance(user, User) or card is None:
            return None
        with DbSession.atomic() as db:
            # Lock before resolving current visibility; a cached Card is not authority.
            current = db.exec(
                SqlBuilder.select.table(Card)
                .where(
                    Card.id == InfraHelper.convert_id(card),
                )
                .with_for_update()
            ).first()
            if current is None:
                return None
            resolved = self.resolve_readable_card(project, current, user, channel)
            if resolved is None:
                return None
            _, current, _ = resolved
            state = self.repo.user_card_read_state.set_read_state(user, current, seen)
            db.after_commit(lambda: CardPublisher.read_state_changed(current, user))
            return {"card_uid": current.get_uid(), "seen_change_seq": state.seen_change_seq}

    def get_by_id_like(self, card: TCardParam | None) -> Card | None:
        card = InfraHelper.get_by_id_like(Card, card)
        return card

    def get_existing_uids(
        self,
        project: TProjectParam,
        card_uids: list[str],
        *,
        user: User,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> list[str]:
        """Resolve current server facts before exposing even card existence."""
        if len(card_uids) > 200:
            raise ValueError("At most 200 recent cards may be checked")
        if not card_uids or not isinstance(user, User):
            return []
        project_uid = InfraHelper.convert_uid(InfraHelper.convert_id(project))
        return [
            card_uid
            for _, card_uid in self.get_existing_pairs(
                [(project_uid, uid) for uid in card_uids],
                user=user,
                channel=channel,
            )
        ]

    def get_existing_pairs(
        self,
        pairs: list[tuple[str, str]],
        *,
        user: User,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> list[tuple[str, str]]:
        """Validate only explicitly requested board/card pairs using current authority."""
        if len(pairs) > 200:
            raise ValueError("At most 200 recent cards may be checked")
        pairs = list(dict.fromkeys(pairs))
        # Invalid/unknown external identifiers fail closed before SQL conversion.
        valid_pairs = []
        for project_uid, card_uid in pairs:
            try:
                if InfraHelper.convert_id(project_uid) > 0 and InfraHelper.convert_id(card_uid) > 0:
                    valid_pairs.append((project_uid, card_uid))
            except ValueError:
                continue
        pairs = valid_pairs
        if not pairs or not isinstance(user, User):
            return []
        project_ids = {InfraHelper.convert_id(project_uid) for project_uid, _ in pairs}
        contexts = self.resolve_work_visibility_contexts(user, channel, project_ids=project_ids)
        return self.repo.card.get_existing_pairs(pairs, contexts=contexts)

    def resolve_visibility_context(
        self,
        project: TProjectParam,
        user: User,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> tuple[Project, CardVisibilityContext] | None:
        """Resolve current project-scoped identity; unknown callers fail closed."""
        if not isinstance(user, User):
            return None
        # Read the primary: cached auth and replica membership may outlive revocation.
        project_id = InfraHelper.convert_id(project)
        assigned = (
            select(ProjectAssignedUser.id)
            .where(ProjectAssignedUser.project_id == Project.id, ProjectAssignedUser.user_id == user.id)
            .exists()
        )
        with DbSession.use(readonly=False) as db:
            current = db.exec(
                select(Project, User, assigned)
                .select_from(Project)
                .join(User, User.id == user.id)
                .where(Project.id == project_id)
                .execution_options(populate_existing=True)
            ).first()
        if current is None:
            return None
        project, current_user, assigned_member = current
        if project.deleted_at is not None or current_user.deleted_at is not None or not current_user.activated_at:
            return None
        member = project.owner_id == current_user.id or bool(assigned_member)
        internal = self._resolve_internal_access(current_user) if member else False
        context = CardVisibilityContext(
            channel=channel,
            active=True,
            project_member=member,
            internal_member=internal,
            actor_user_id=int(current_user.id),
        )
        return project, context

    def resolve_work_visibility_contexts(
        self,
        user: User,
        channel: CollaborationChannel,
        *,
        project_ids: set[int] | None = None,
    ) -> dict[int, CardVisibilityContext]:
        """Resolve current cross-board membership in two primary reads, not N reads."""
        if not isinstance(user, User) or project_ids == set():
            return {}
        with DbSession.use(readonly=False) as db:
            current = db.exec(
                SqlBuilder.select.table(User).where(User.id == user.id).execution_options(populate_existing=True)
            ).first()
            if current is None or current.deleted_at is not None or not current.activated_at:
                return {}
            membership = (
                select(ProjectAssignedUser.id)
                .where(
                    ProjectAssignedUser.project_id == Project.id,
                    ProjectAssignedUser.user_id == current.id,
                )
                .exists()
            )
            actions = "," + sql_cast(ProjectRole.actions, Text) + ","
            read_grant = (
                select(ProjectRole.id)
                .where(
                    ProjectRole.project_id == Project.id,
                    ProjectRole.user_id == current.id,
                    or_(actions.contains(",*,"), actions.contains(",read,")),
                )
                .exists()
            )
            query = SqlBuilder.select.column(Project.id).where(
                or_(Project.owner_id == current.id, membership & or_(current.is_admin, read_grant)),
            )
            if project_ids is not None:
                query = query.where(Project.id.in_(project_ids))
            projects = db.exec(query).all()
        internal = self._resolve_internal_access(current)
        context = CardVisibilityContext(channel, True, True, internal, actor_user_id=int(current.id))
        return {int(project_id): context for project_id in projects}

    def _resolve_internal_access(self, user: User) -> bool | None:
        """Resolve card access independently of employee classification.

        Callers separately validate current account and project membership.
        This explicit compatibility policy retains existing board ACLs for
        deployments that have not provisioned an employee directory.
        """
        if Env.CARD_INTERNAL_ACCESS_MODE == "project_members":
            return True
        if Env.CARD_INTERNAL_ACCESS_MODE == "scim":
            return self._get_service(ScimProvisioningService).is_employee(user)
        if Env.CARD_INTERNAL_ACCESS_MODE == "oidc_issuers":
            scim = self._get_service(ScimProvisioningService)
            if scim._employee_policy() is not None:
                return scim.is_employee(user)
            link = self.repo.user_identity_link.get_by_user_provider(user, IdentityProvider.Oidc, consistent=True)
            return bool(link and link.external_id and link.issuer.rstrip("/") in Env.CARD_INTERNAL_OIDC_ISSUERS)
        return False

    def get_by_project(self, project: TProjectParam | None) -> list[Card]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return []

        return [card for card, _ in self.repo.card.get_all_by_project(project)]

    def get_visible_by_project(
        self,
        project: TProjectParam,
        user: TUserOrBot,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> list[Card]:
        resolved = self.resolve_visibility_context(project, user, channel)
        if resolved is None:
            return []
        project, context = resolved
        return [card for card, _ in self.repo.card.get_all_by_project(project, context=context)]

    def resolve_readable_card(
        self,
        project: TProjectParam | None,
        card: TCardParam | None,
        user: TUserOrBot | None,
        channel: CollaborationChannel = CollaborationChannel.Api,
        *,
        metadata_only: bool = False,
    ) -> tuple[Project, Card, CardVisibilityContext] | None:
        """Revalidate direct IDs and cached rows; metadata projections forbid body loading."""
        if card is None:
            return None
        query = SqlBuilder.select.table(Card).where(Card.id == InfraHelper.convert_id(card))
        if metadata_only:
            query = query.options(defer(Card.description, raiseload=True), defer(Card.ai_description, raiseload=True))
        with DbSession.use(readonly=False) as db:
            current = db.exec(query.execution_options(populate_existing=True)).first()
        if current is None or current.deleted_at is not None:
            return None
        resolved = self.resolve_visibility_context(
            project if project is not None else current.project_id, user, channel
        )
        if resolved is None:
            return None
        project, context = resolved
        if current.project_id != project.id or not context.can_read_card(
            CardVisibility(current.visibility),
            owner_user_id=current.owner_user_id,
        ):
            return None
        return project, current, context

    def resolve_canonical_card(
        self,
        requested_project,
        card,
        user,
        channel=CollaborationChannel.Api,
        *,
        metadata_only: bool = False,
    ):
        """Authorize current location before returning a historical URL redirect."""
        from ..CardCrossBoardOperations import _grant

        resolved = self.resolve_readable_card(None, card, user, channel, metadata_only=metadata_only)
        if resolved is None:
            return None
        project, current, context = resolved
        with DbSession.use(readonly=False) as db:
            if not _grant(db, user, project, ProjectRoleAction.Read):
                return None
            if InfraHelper.convert_id(requested_project) != project.id:
                receipt = db.exec(
                    SqlBuilder.select.table(ProjectActivity)
                    .where(
                        ProjectActivity.card_id == current.id,
                        ProjectActivity.activity_type == ProjectActivityType.CardMoved,
                        ProjectActivity.activity_history["source"]["project_uid"].as_string()
                        == InfraHelper.convert_uid(InfraHelper.convert_id(requested_project)),
                    )
                    .limit(1)
                ).first()
                if receipt is None:
                    return None
        return project, current, context

    def get_readable_card_project_uids(
        self,
        card_uids: Sequence[str],
        user: User,
        channel: CollaborationChannel,
    ) -> dict[str, str]:
        """Apply current card policy in bulk without loading card bodies."""
        if len(card_uids) > 200:
            raise ValueError("At most 200 cards may be checked")
        if not card_uids or not isinstance(user, User):
            return {}
        card_ids = []
        for uid in set(card_uids):
            try:
                card_ids.append(InfraHelper.convert_id(uid))
            except (TypeError, ValueError):
                continue
        if not card_ids:
            return {}
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)).first()
            if current is None or current.deleted_at is not None or not current.activated_at:
                return {}
            membership = (
                select(ProjectAssignedUser.id)
                .where(
                    ProjectAssignedUser.project_id == Project.id,
                    ProjectAssignedUser.user_id == current.id,
                )
                .exists()
            )
            rows = db.exec(
                SqlBuilder.select.columns(Card.id, Card.project_id, Card.visibility, Card.owner_user_id)
                .join(Project, Project.id == Card.project_id)
                .where(
                    Card.id.in_(card_ids), Project.deleted_at.is_(None), or_(Project.owner_id == current.id, membership)
                )
            ).all()
        context = CardVisibilityContext(
            channel,
            True,
            True,
            self._resolve_internal_access(current),
            actor_user_id=int(current.id),
        )
        return {
            card_id.to_short_code(): project_id.to_short_code()
            for card_id, project_id, visibility, owner_id in rows
            if context.can_read_card(CardVisibility(visibility), owner_user_id=owner_id)
        }

    def get_work_states(
        self,
        cards: Sequence[Card],
        *,
        context: CardVisibilityContext | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Project a permission-scoped card batch with bounded queries, never per-card reads.

        Callers must supply cards from their existing authorized query. No hidden
        related-card data, task metadata or user identities enter this projection.
        """
        if not cards:
            return {}
        column_ids = {card.project_column_id for card in cards}
        with DbSession.use(readonly=True) as db:
            column_policies = {
                column.id: (column, policy)
                for column, policy in db.exec(
                    SqlBuilder.select.tables(ProjectColumn, WorkflowStageDefinition)
                    .outerjoin(WorkflowStageDefinition, ProjectColumn.workflow_stage == WorkflowStageDefinition.key)
                    .options(
                        load_only(
                            ProjectColumn.id,
                            ProjectColumn.project_id,
                            ProjectColumn.workflow_stage,
                            ProjectColumn.is_archive,
                            raiseload=True,
                        ),
                        load_only(
                            WorkflowStageDefinition.id,
                            WorkflowStageDefinition.key,
                            WorkflowStageDefinition.counts_as_completed,
                            WorkflowStageDefinition.active_queue_policy,
                            WorkflowStageDefinition.overdue_policy,
                            raiseload=True,
                        ),
                    )
                    .where(ProjectColumn.column("id").in_(column_ids))
                ).all()
            }
        counts = self.repo.checkitem.get_work_state_counts([card.id for card in cards])
        verification_records = self.repo.card_verification.get_latest_by_card_ids([card.id for card in cards])
        blockers = dependency_blockers([card.id for card in cards], context=context)
        generations = execution_generations([card.id for card in cards])
        approvals = pending_card_approvals([card.id for card in cards])
        signals = card_signal_projections(cards) if context is not None else {}
        states = {}
        for card in cards:
            column, policy = column_policies.get(card.project_column_id, (None, None))
            stage = column.workflow_stage if column and column.project_id == card.project_id else None
            policy = policy if stage else None
            total, completed, started, paused = counts.get(card.id, (0, 0, 0, 0))
            record = verification_records.get(card.id)
            states[card.id] = project_work_state(
                card_uid=card.get_uid(),
                workflow_stage=stage,
                workflow_policy={
                    "key": policy.key,
                    "counts_as_completed": policy.counts_as_completed,
                    "active_queue_policy": policy.active_queue_policy,
                    "overdue_policy": policy.overdue_policy,
                }
                if policy
                else None,
                archived=card.archived_at is not None
                or bool(column and column.project_id == card.project_id and column.is_archive),
                linked_resource=card.is_linked_resource,
                total=total,
                completed=completed,
                started=started,
                paused=paused,
                change_seq=card.last_change_seq,
                execution_generation=generations.get(int(card.id)),
                pending_approval_count=approvals.get(int(card.id), 0),
                verification_record=self._verification_projection(record) if record else None,
                direct_blockers=blockers.get(int(card.id)),
                external_signals=signals.get(card.id, []) if context is not None else None,
            )
            states[card.id]["dependency_state"]["scope"] = "readable_relationships" if context else None
        return states

    def publish_work_states(self, project: Project, card_ids: Sequence[SnowflakeID]) -> None:
        """Refresh authorized card projections after dependency mutations."""
        if not card_ids:
            return
        # Nested projection reads must observe the same primary as the cards.
        with DbSession.atomic() as db:
            cards = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.column("id").in_(set(card_ids)))
                .where(Card.column("project_id") == project.id)
                .where(Card.column("deleted_at").is_(None))
            ).all()
            states = self.get_work_states(cards)
        for card in cards:
            CardPublisher.updated(project, card, None, {"work_state": states[card.id]})

    def _dependency_children(self, card: Card) -> list[SnowflakeID]:
        return [
            child.id
            for _, relation_type, child in self.repo.card_relationship.get_all_by_card_and_relation(
                card, relation="child"
            )
            if relation_type.machine_semantic == "blocks" and child.project_id == card.project_id
        ]

    @staticmethod
    def _verification_projection(record: CardVerificationRecord) -> dict[str, Any]:
        return {
            "uid": record.get_uid(),
            "source_change_seq": record.source_change_seq,
            "decision": record.decision,
            "recorded_at": record.created_at.isoformat(),
            "recorded_by_user_uid": record.recorded_by_user_id.to_short_code() if record.recorded_by_user_id else None,
            "recorded_by_bot_uid": record.recorded_by_bot_id.to_short_code() if record.recorded_by_bot_id else None,
            "evidence": record.evidence,
            "required_checkitem_uids": record.required_checkitem_uids,
        }

    def record_verification_evidence(
        self, actor: User | Bot, project: TProjectParam, card: TCardParam, submission: VerificationSubmission
    ) -> dict[str, Any] | None:
        """Append reviewer evidence with a card lock and optimistic record fence.

        Authorization belongs to the native route. This records declared scope,
        never changes workflow/assignment/timers and never approves a release.
        """
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        with DbSession.atomic() as db:
            current = db.exec(
                SqlBuilder.select.table(Card)
                .where((Card.column("id") == card.id) & (Card.column("project_id") == project.id))
                .with_for_update()
            ).first()
            if current is None or current.deleted_at is not None or current.archived_at or current.is_linked_resource:
                return None
            if current.last_change_seq != submission.expected_change_seq:
                raise VerificationConflict("Card changed; read current work_state before recording verification")
            latest = self.repo.card_verification.get_latest_by_card_ids([current.id]).get(current.id)
            if (latest.get_uid() if latest else None) != submission.expected_record_uid:
                raise VerificationConflict("Verification changed; read its latest record before replacing it")
            items = db.exec(
                SqlBuilder.select.table(Checkitem)
                .join(Checklist, Checkitem.column("checklist_id") == Checklist.column("id"))
                .where(Checklist.column("card_id") == current.id)
                .where(Checklist.column("is_system") == False)  # noqa: E712
                .where(Checklist.column("deleted_at").is_(None))
                .where(Checkitem.column("deleted_at").is_(None))
            ).all()
            by_uid = {item.get_uid(): item for item in items}
            referenced = set(submission.required_checkitem_uids) | {
                entry.checkitem_uid for entry in submission.evidence if entry.checkitem_uid is not None
            }
            if not referenced.issubset(by_uid):
                raise ValueError("Evidence checkitem is not an active user checkitem of this card")
            if submission.decision == "verified" and any(
                not by_uid[uid].is_checked for uid in submission.required_checkitem_uids
            ):
                raise ValueError("Declared required checkitems are not complete")
            if not isinstance(actor, (User, Bot)):
                raise ValueError("Authenticated reviewer is required")
            record = CardVerificationRecord(
                card_id=current.id,
                source_change_seq=current.last_change_seq,
                decision=submission.decision,
                recorded_by_user_id=actor.id if isinstance(actor, User) else None,
                recorded_by_bot_id=actor.id if isinstance(actor, Bot) else None,
                evidence=[entry.model_dump(mode="json") for entry in submission.evidence],
                required_checkitem_uids=submission.required_checkitem_uids,
            )
            db.insert(record)
            state = self.get_work_states([current])[current.id]
        # Evidence is its own record revision, so it must not invalidate its
        # subject by advancing the card's source cursor.
        CardPublisher.updated(project, current, None, {"work_state": state})
        return self._verification_projection(record)

    @staticmethod
    def cross_board_eligibility(cards: Sequence[Card]) -> dict[int, bool]:
        """Project ownership only for card rows already authorized by the caller."""
        card_ids = list(dict.fromkeys(card.id for card in cards))
        owned = set()
        with DbSession.use(readonly=False) as db:
            for offset in range(0, len(card_ids), 200):
                rows = db.exec(
                    select(CardAppOwnership.card_id).where(
                        CardAppOwnership.card_id.in_(card_ids[offset : offset + 200]),
                        CardAppOwnership.app_key.is_not(None),
                    )
                ).all()
                owned.update(row[0] for row in rows)
        return {card.id: not card.is_linked_resource and card.id not in owned for card in cards}

    def get_details(
        self,
        project: TProjectParam | None,
        card: TCardParam | None,
        user_or_bot: TUserOrBot | None = None,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        include_description: bool = True,
    ) -> dict[str, Any] | None:
        resolved = self.resolve_readable_card(
            project,
            card,
            user_or_bot,
            channel,
            metadata_only=not include_description,
        )
        if resolved is None:
            return None
        project, card, context = resolved

        column = InfraHelper.get_by_id_like(ProjectColumn, card.project_column_id)
        if not column:
            return None

        api_card = card.api_response(include_body=include_description)
        api_card["can_cross_board"] = self.cross_board_eligibility([card])[card.id]
        api_card["visibility"] = card.visibility
        api_card["project_column_name"] = column.name
        api_card["work_state"] = self.get_work_states([card], context=context)[card.id]
        progress = api_card["work_state"]["checklist_progress"]
        api_card["checklist_total_count"] = progress["total"]
        api_card["checklist_completed_count"] = progress["completed"]
        if card.is_linked_resource:
            api_card.update(
                {
                    "count_comment": 0,
                    "project_members": [],
                    "labels": [],
                    "member_uids": [],
                    "relationships": [],
                    "linked_resource": self._get_linked_resource_payloads(
                        user_or_bot,
                        project,
                        [card],
                        include_content=include_description,
                        **({"channel": channel} if channel != CollaborationChannel.Api else {}),
                    )[card.get_uid()],
                }
            )
            return api_card

        api_card["count_comment"] = self.repo.card_comment.count_by_card(card)
        api_card["is_check_card"] = self.is_check_card(card, metadata_only=not include_description)
        completion = self._get_completion_checklist(card)
        api_card["completed"] = bool(completion and completion.is_checked)

        project_service = self._get_service(ProjectService)
        api_card["project_members"] = project_service.get_api_assigned_user_list(card.project_id)

        project_label_service = self._get_service(ProjectLabelService)
        api_card["labels"] = project_label_service.get_api_list_by_card(card)

        api_card["member_uids"] = self.get_api_assigned_user_list(card, only_uids=True)
        api_card["active_workers"] = self.get_active_workers(project, card).get(card.id, [])

        card_relationship_service = self._get_service(CardRelationshipService)
        api_card["relationships"] = card_relationship_service.get_api_list_by_card(card, context=context)

        if include_description:
            blocks = self._get_service(CardContentBlockService).api_blocks_by_card(card)
            if blocks:
                api_card["content_blocks"] = blocks
                api_card["description_content_source"] = "blocks"
        return api_card

    @card_change_snapshot
    def get_unread_card_summaries(
        self,
        project: TProjectParam,
        user_or_bot: User | None = None,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict | None:
        """Project unread summaries in the native authority/read snapshot."""
        if not isinstance(user_or_bot, User):
            return None
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid unread summary limit")
        resolved = self.resolve_visibility_context(project, user_or_bot, channel)
        if resolved is None or not resolved[1].project_member:
            return None
        project, context = resolved
        binding = [str(user_or_bot.id), project.get_uid(), channel.value]
        before = None
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 512:
                    raise ValueError()
                fields = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                before = fields["before"]
                if (
                    fields.get("context") != binding
                    or not isinstance(before, list)
                    or len(before) != 2
                    or any(type(value) is not int or value < 0 or value > 2**63 - 1 for value in before)
                    or before[1] == 0
                ):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError, Base64Error, UnicodeDecodeError) as exc:
                raise ValueError("Invalid unread summary cursor") from exc
        rows = self.repo.user_card_read_state.get_unread_summary_page(
            user_or_bot.id,
            project.id,
            context=context,
            limit=limit,
            before=before,
            archive_visible_since=SafeDateTime.now() - timedelta(days=project.archive_visible_days),
        )
        page = rows[:limit]
        states = self.repo.user_card_read_state.get_change_state_map(
            user_or_bot.id,
            [row[0] for row in page],
            context=context,
        )
        items = [
            {"uid": card_id.to_short_code(), "project_uid": project.get_uid(), "title": title, **states[card_id]}
            for card_id, title, _ in page
            if card_id in states
        ]
        next_cursor = None
        if len(rows) > limit and page:
            card_id, _, seq = page[-1]
            next_cursor = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "context": binding,
                            "before": [seq, int(card_id)],
                        }
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
        return {"items": items, "next_cursor": next_cursor, "limit": limit}

    @card_change_snapshot
    def get_board_snapshot(
        self,
        project: TProjectParam | None,
        user_or_bot: TUserOrBot | None = None,
        archive_visible_since: SafeDateTime | None = None,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        phase_timings: dict[str, float] | None = None,
    ) -> tuple[list[dict[str, Any]], list[Checklist]]:
        """Return board cards and their already-read user checklist rows together."""
        last_phase_at = monotonic_ns() if phase_timings is not None else 0

        def mark_phase(name: str) -> None:
            nonlocal last_phase_at
            if phase_timings is None:
                return
            now = monotonic_ns()
            phase_timings[name] = max(0, now - last_phase_at) / 1_000_000
            last_phase_at = now

        resolved = self.resolve_visibility_context(project, user_or_bot, channel)
        mark_phase("snapshot_context")
        if resolved is None:
            return [], []

        project, context = resolved
        if archive_visible_since is None:
            archive_visible_since = SafeDateTime.now() - timedelta(days=project.archive_visible_days)
        raw_cards = self.repo.card.get_board_list(project, archive_visible_since, context=context)
        mark_phase("snapshot_rows")
        if not raw_cards:
            return [], []
        visible_cards = {card.id: card for card, _ in raw_cards}
        eligibility = self.cross_board_eligibility(list(visible_cards.values()))
        raw_members = self.repo.card_assigned_user.get_all_by_project(project, archive_visible_since, context=context)
        members: dict[int, list[str]] = {}
        for member, card_assigned_user in raw_members:
            if card_assigned_user.card_id not in members:
                members[card_assigned_user.card_id] = []
            members[card_assigned_user.card_id].append(member.get_uid())
        mark_phase("snapshot_members")

        relationships = self._get_service(CardRelationshipService).get_readable_incident_by_cards(
            list(visible_cards.values()), context, board_project_id=project.id
        )
        mark_phase("snapshot_relations")

        raw_labels = self.repo.project_label.get_all_card_labels_by_project(
            project, archive_visible_since, context=context
        )
        labels: dict[int, list[dict[str, Any]]] = {}
        for label, card_label in raw_labels:
            if card_label.card_id not in labels:
                labels[card_label.card_id] = []
            labels[card_label.card_id].append(label.api_response())
        mark_phase("snapshot_labels")

        creators = self.repo.card.get_board_creators(project, archive_visible_since, context=context)
        mark_phase("snapshot_creators")
        raw_checklists = self.repo.checklist.get_all_by_project(
            project,
            archive_visible_since=archive_visible_since,
            context=context,
        )
        completed_by_card = {
            checklist.card_id: checklist.is_checked for checklist in raw_checklists if checklist.is_system
        }
        user_checklist_card_ids = {checklist.card_id for checklist in raw_checklists if not checklist.is_system}
        mark_phase("snapshot_checklists")
        active_workers_by_card = self.get_active_workers(project, context=context)
        mark_phase("snapshot_workers")
        work_states = self.get_work_states([card for card, _ in raw_cards], context=context)
        mark_phase("snapshot_work_states")

        user = user_or_bot if isinstance(user_or_bot, User) else None
        change_states = {}
        if user is not None:
            change_states = self.repo.user_card_read_state.get_change_state_map(
                user.id, [card.id for card, _ in raw_cards], context=context
            )
            assigned = self.repo.project_assigned_user.get_by_user_and_project(user, project)
            board_seq = max((card.last_change_seq for card, _ in raw_cards), default=0)
            if assigned is not None and board_seq > (assigned.board_seen_seq or 0):
                # Keep the MVCC snapshot read-only; concurrent board visits must
                # not turn this best-effort dashboard watermark into a 40001.
                with DbSession.use(readonly=False) as db:
                    db.after_commit(
                        lambda: self.repo.project_assigned_user.advance_board_seen(user, project, board_seq)
                    )
        mark_phase("snapshot_unread")

        cards = []
        linked_cards = [card for card, _ in raw_cards if getattr(card, "is_linked_resource", False)]
        resource_payloads = (
            self._get_linked_resource_payloads(
                user_or_bot,
                project,
                linked_cards,
                include_content=False,
                **({"channel": channel} if channel != CollaborationChannel.Api else {}),
            )
            if linked_cards
            else {}
        )
        mark_phase("snapshot_linked")
        for card, count_comment in raw_cards:
            is_check_card = (
                bool(card.deadline_at) or not card.description.content.strip()
            ) and card.id not in user_checklist_card_ids
            api_card = card.board_api_response(
                count_comment=count_comment,
                member_uids=members.get(card.id, []),
                relationships=relationships.get(card.id, []),
                labels=labels.get(card.id, []),
                creator=self._card_creator_projection(card, creators.get(card.id)),
                completed=completed_by_card.get(card.id, False),
                is_check_card=is_check_card,
                include_body=False,
            )
            api_card["can_cross_board"] = eligibility[card.id]
            progress = work_states[card.id]["checklist_progress"]
            api_card["checklist_total_count"] = progress["total"]
            api_card["checklist_completed_count"] = progress["completed"]
            api_card["active_workers"] = active_workers_by_card.get(card.id, [])
            api_card["work_state"] = work_states[card.id]
            if getattr(card, "is_linked_resource", False):
                api_card["linked_resource"] = resource_payloads[card.get_uid()]
            if user is not None:
                if card.id not in change_states:
                    continue
                api_card.update(change_states[card.id])
            cards.append(api_card)

        mark_phase("snapshot_projection")
        return cards, [checklist for checklist in raw_checklists if not checklist.is_system]

    def get_active_workers(
        self, project: Project, card: Card | None = None, *, context: CardVisibilityContext | None = None
    ) -> dict[int, list[dict[str, Any]]]:
        """Project one avatar per timer owner without changing card assignment."""
        grouped: dict[int, dict[str, dict[str, Any]]] = {}
        now = SafeDateTime.now()
        for checkitem, checklist, started_at in self.repo.checkitem.get_active_workers_by_project(
            project, card, context=context
        ):
            if checkitem.user_id is None:
                continue
            user_uid = checkitem.user_id.to_short_code()
            worker = grouped.setdefault(checklist.card_id, {}).setdefault(
                user_uid,
                {"user_uid": user_uid, "status": "paused", "started_at": None, "elapsed_seconds": 0, "checkitems": []},
            )
            elapsed = checkitem.accumulated_seconds
            if started_at is not None:
                elapsed += max(0, int((now - started_at).total_seconds()))
            worker["elapsed_seconds"] += elapsed
            worker["checkitems"].append(
                {
                    "uid": checkitem.get_uid(),
                    "title": checkitem.title,
                    "status": checkitem.status.value,
                    "started_at": started_at.isoformat() if started_at is not None else None,
                    "elapsed_seconds": elapsed,
                    "sampled_at": now.isoformat(),
                }
            )
            if checkitem.status == CheckitemStatus.Started:
                worker["status"] = "started"
                if (
                    worker["started_at"] is None
                    or started_at is not None
                    and started_at.isoformat() < worker["started_at"]
                ):
                    worker["started_at"] = started_at.isoformat() if started_at is not None else None
        for workers in grouped.values():
            for worker in workers.values():
                first = worker["checkitems"][0]
                worker["checkitem_uid"] = first["uid"]
                worker["title"] = first["title"]
        return {card_id: list(workers.values()) for card_id, workers in grouped.items()}

    def _card_creator_projection(self, card: Card, creator: User | Bot | None) -> dict[str, Any] | None:
        """Return only display-safe author data for the dense board list."""

        if creator is None:
            return None

        return {
            "uid": creator.get_uid(),
            "type": User.USER_TYPE if isinstance(creator, User) else Bot.BOT_TYPE,
            "name": creator.get_fullname(),
            "avatar": (
                creator.avatar.path
                if creator.avatar is not None and (not isinstance(creator, User) or creator.deleted_at is None)
                else None
            ),
            "created_at": card.created_at.isoformat(),
        }

    def _get_linked_resource_payloads(
        self,
        user_or_bot: TUserOrBot | None,
        project: Project,
        cards: Sequence[Card],
        *,
        include_content: bool,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> dict[str, dict[str, Any]]:
        payloads: dict[str, dict[str, Any]] = {
            card.get_uid(): {
                "type": card.source_type,
                "uid": card.source_uid,
                "status": "missing",
            }
            for card in cards
            if card.source_type is not None and card.source_uid is not None
        }
        from ..CardCrossBoardOperations import project_card_payloads

        payloads.update(
            project_card_payloads(
                self,
                user_or_bot,
                [card for card in cards if card.source_type == Card.LINKED_RESOURCE_PROJECT_CARD],
                include_content=include_content,
                channel=channel,
                destination_project=project,
            )
        )
        wiki_cards = [
            card
            for card in cards
            if card.source_type == Card.LINKED_RESOURCE_PROJECT_WIKI and card.source_uid is not None
        ]
        wiki_uids = {cast(str, card.source_uid) for card in wiki_cards}
        if not wiki_cards:
            return payloads
        if include_content:
            wikis = self.repo.project_wiki.get_by_project_and_uids(project, wiki_uids)
            wiki_map: dict[str, Any] = {wiki.get_uid(): wiki for wiki in wikis}
            private_wiki_ids = {int(wiki.id) for wiki in wikis if not wiki.is_public}
        else:
            headers = self.repo.project_wiki.get_headers_by_project_and_uids(project, wiki_uids)
            wiki_map = {
                wiki_id.to_short_code(): {"id": wiki_id, "title": title, "is_public": is_public}
                for wiki_id, title, is_public in headers
            }
            private_wiki_ids = {int(wiki_id) for wiki_id, _, is_public in headers if not is_public}

        assigned_wiki_ids: set[int] = set()
        can_view_all = isinstance(user_or_bot, Bot) or (
            isinstance(user_or_bot, User) and (user_or_bot.is_admin or project.owner_id == user_or_bot.id)
        )
        if isinstance(user_or_bot, User) and not can_view_all:
            assigned_wiki_ids = self.repo.project_wiki_assigned_user.get_assigned_wiki_ids(
                user_or_bot,
                private_wiki_ids,
            )

        for card in wiki_cards:
            source_uid = cast(str, card.source_uid)
            wiki = wiki_map.get(source_uid)
            payload: dict[str, Any] = {
                "type": Card.LINKED_RESOURCE_PROJECT_WIKI,
                "uid": source_uid,
                "status": "missing",
            }
            if wiki is not None:
                wiki_id = wiki.id if include_content else wiki["id"]
                is_public = wiki.is_public if include_content else wiki["is_public"]
                title = wiki.title if include_content else wiki["title"]
                can_view = is_public or can_view_all or int(wiki_id) in assigned_wiki_ids
                if can_view:
                    payload.update(
                        {
                            "status": "available",
                            "title": title,
                        }
                    )
                    if include_content:
                        content = wiki.content.content if wiki.content else ""
                        payload["preview"] = content[: self.LINKED_RESOURCE_PREVIEW_MAX_LENGTH]
                        payload["content"] = convert_python_data(wiki.content)
                else:
                    payload["status"] = "forbidden"
            payloads[card.get_uid()] = payload

        return payloads

    def link_cross_board_card(
        self,
        user,
        source_project,
        source_card,
        project,
        column,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ):
        from ..CardCrossBoardOperations import link_card

        return link_card(self, user, source_project, source_card, project, column, channel=channel)

    def clone_cross_board_card(
        self,
        user,
        source_project,
        source_card,
        project,
        column,
        *,
        request_uid,
        confirmation_token=None,
        channel=CollaborationChannel.Api,
    ):
        from ..CardCloneOperations import clone_card

        return clone_card(
            self,
            user,
            source_project,
            source_card,
            project,
            column,
            request_uid=request_uid,
            confirmation_token=confirmation_token,
            channel=channel,
        )

    def move_cross_board_card(
        self,
        user,
        source_project,
        source_card,
        project,
        column,
        *,
        request_uid,
        confirmation_token=None,
        channel=CollaborationChannel.Api,
    ):
        from ..CardMoveOperations import move_card

        return move_card(
            self,
            user,
            source_project,
            source_card,
            project,
            column,
            request_uid=request_uid,
            confirmation_token=confirmation_token,
            channel=channel,
        )

    def create_linked_wiki_card(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        wiki: TWikiParam | None,
        column: TColumnParam | None = None,
    ) -> tuple[Card, dict[str, Any], bool] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return None
        project, wiki = params
        if wiki.project_id != project.id or not wiki.is_public:
            return None
        if column is None:
            column = next(
                (
                    candidate
                    for candidate, _ in self.repo.project_column.get_all_by_project(project)
                    if not candidate.is_archive
                ),
                None,
            )
        else:
            column = InfraHelper.get_by_id_like(ProjectColumn, column)
        if column is None or column.project_id != project.id or column.is_archive:
            return None
        if isinstance(user_or_bot, User):
            can_view = (
                user_or_bot.is_admin
                or project.owner_id == user_or_bot.id
                or self._get_service(ProjectWikiService).is_assigned(user_or_bot, wiki)
            )
            if not can_view:
                return None

        source_uid = wiki.get_uid()
        existing = self.repo.card.find_linked_resource(project, Card.LINKED_RESOURCE_PROJECT_WIKI, source_uid)
        if existing is not None:
            payload = existing.board_api_response(0, [], [], [])
            payload["linked_resource"] = self._get_linked_resource_payloads(
                user_or_bot, project, [existing], include_content=False
            )[existing.get_uid()]
            return existing, payload, False

        card = Card(
            created_by_user_id=user_or_bot.id if isinstance(user_or_bot, User) else None,
            created_by_bot_id=user_or_bot.id if isinstance(user_or_bot, Bot) else None,
            project_id=project.id,
            project_column_id=column.id,
            title="",
            description=EditorContentModel(),
            order=self.repo.card.get_next_order(column, {"project_id": project.id}),
            source_type=Card.LINKED_RESOURCE_PROJECT_WIKI,
            source_uid=source_uid,
        )
        try:
            self.repo.card.insert(card)
        except IntegrityError:
            existing = self.repo.card.find_linked_resource(project, Card.LINKED_RESOURCE_PROJECT_WIKI, source_uid)
            if existing is None:
                raise
            payload = existing.board_api_response(0, [], [], [])
            payload["linked_resource"] = self._get_linked_resource_payloads(
                user_or_bot, project, [existing], include_content=False
            )[existing.get_uid()]
            return existing, payload, False

        payload = card.board_api_response(0, [], [], [])
        payload["linked_resource"] = self._get_linked_resource_payloads(
            user_or_bot, project, [card], include_content=False
        )[card.get_uid()]
        CardPublisher.created(project, column, {"card": payload})
        return card, payload, True

    def get_api_archived_page_by_project(
        self,
        project: TProjectParam | None,
        limit: int,
        before_archived_at: SafeDateTime | None = None,
        before_card: TCardParam | None = None,
        input_value: str | None = None,
        *,
        user_or_bot: TUserOrBot | None = None,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> tuple[list[dict[str, Any]], int, tuple[str, str] | None] | None:
        """Return an archive-only page kept separate from the board hot path."""

        resolved = self.resolve_visibility_context(project, user_or_bot, channel)
        if resolved is None:
            return None
        project, context = resolved
        records = self.repo.card.get_archived_page_by_project(
            project,
            limit,
            before_archived_at,
            before_card,
            input_value,
            context=context,
        )
        has_more = len(records) > limit
        page = records[:limit]
        cards: list[dict[str, Any]] = []
        for card, column in page:
            api_card = card.api_response()
            api_card["project_column_name"] = column.name
            cards.append(api_card)
        next_fields = None
        if has_more and page:
            last_card = page[-1][0]
            next_fields = (last_card.archived_at.isoformat(), last_card.get_uid())
        return cards, self.repo.card.count_archived_by_project(project, input_value, context=context), next_fields

    def get_dashboard_list(
        self,
        user: User,
        pagination: TimeBasedPagination,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        contexts = self.resolve_work_visibility_contexts(user, channel)
        records = self.repo.card.get_dashboard_list_scroller(user, pagination, contexts=contexts)
        work_states = self.get_work_states([card for card, *_ in records], context=next(iter(contexts.values()), None))

        api_cards = []
        api_projects: dict[int, dict[str, Any]] = {}
        linked_resources_by_project: dict[int, tuple[Project, list[Card], list[dict[str, Any]]]] = {}
        for card, project, column in records:
            api_card = card.api_response()
            api_card["project_column_name"] = column.name
            api_card["work_state"] = work_states[card.id]
            if card.is_linked_resource:
                resource_group = linked_resources_by_project.setdefault(project.id, (project, [], []))
                resource_group[1].append(card)
                resource_group[2].append(api_card)
            if project.id not in api_projects:
                api_projects[project.id] = project.api_response()
            api_cards.append(api_card)

        for project, linked_cards, linked_api_cards in linked_resources_by_project.values():
            payloads = self._get_linked_resource_payloads(
                user,
                project,
                linked_cards,
                include_content=False,
                **({"channel": channel} if channel != CollaborationChannel.Api else {}),
            )
            for card, api_card in zip(linked_cards, linked_api_cards, strict=True):
                api_card["linked_resource"] = payloads[card.get_uid()]

        return api_cards, list(api_projects.values())

    def get_api_list_by_project(
        self,
        project: TProjectParam | None,
        user_or_bot: TUserOrBot | None = None,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> list[dict[str, Any]]:
        resolved = self.resolve_visibility_context(project, user_or_bot, channel)
        if resolved is None:
            return []
        project, context = resolved

        records = self.repo.card.get_all_by_project(project, context=context)
        work_states = self.get_work_states([card for card, _ in records], context=context)
        resource_payloads = self._get_linked_resource_payloads(
            user_or_bot,
            project,
            [card for card, _ in records if card.is_linked_resource],
            include_content=False,
            **({"channel": channel} if channel != CollaborationChannel.Api else {}),
        )
        blocks_by_card = self._get_service(CardContentBlockService).api_blocks_by_cards(
            [card.id for card, _ in records]
        )
        cards = []
        for card, column in records:
            api_card = card.api_response()
            api_card["project_column_name"] = column.name
            api_card["work_state"] = work_states[card.id]
            if card.is_linked_resource:
                api_card["linked_resource"] = resource_payloads[card.get_uid()]
            blocks = blocks_by_card.get(card.id, [])
            if blocks:
                api_card["content_blocks"] = blocks
                api_card["description_content_source"] = "blocks"
            cards.append(api_card)
        return cards

    def search_context_by_project(
        self,
        project: TProjectParam | None,
        input_value: str,
        date_field: str = "updated_at",
        since: SafeDateTime | None = None,
        until: SafeDateTime | None = None,
        *,
        include_closed: bool = False,
        workflow_stages: list[str] | None = None,
        include_work_state: bool = False,
        visibility: list[str] | None = None,
        created_by: list[str] | None = None,
        assigned_to: list[str] | None = None,
        limit: int = 20,
        before_updated_at: SafeDateTime | None = None,
        before_card: TCardParam | None = None,
        paginated: bool = False,
        user: User,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        contexts = self.resolve_work_visibility_contexts(user, channel) if project is None else None
        if (
            project is not None
            and paginated
            and not self.resolve_work_visibility_contexts(
                user, channel, project_ids={int(InfraHelper.convert_id(project))}
            )
        ):
            return {"cards": [], "total_count": 0, "next_fields": None}
        if project is not None:
            resolved = self.resolve_visibility_context(project, user, channel)
            if resolved is None or not resolved[1].active or not resolved[1].project_member:
                return {"cards": [], "total_count": 0, "next_fields": None} if paginated else []
            project, context = resolved
            scopes = {int(project.id): context}
        else:
            scopes = contexts
        external_candidates, semantic_matches, vector_candidates = {}, {}, {}
        from types import SimpleNamespace
        from ....tasks.docling.CompactionMetadataIndexTask import SemanticSearchRequest
        from ....tasks.docling.DocumentVectorQuery import search_board_documents, validate_board_document_hits
        from .CardCommentCompactionService import CardCommentCompactionService, CommentCompactionError
        from .InternalBotService import InternalBotService

        board_candidates = (
            search_document_candidates_many(
                [SnowflakeID(project_id).to_short_code() for project_id in scopes], input_value
            )
            if project is None
            else None
        )
        with SemanticSearchRequest() as semantic_request:
            for project_id, scoped_context in scopes.items():
                project_code = SnowflakeID(project_id).to_short_code()
                candidates = (
                    (board_candidates or {}).get(project_code)
                    if project is None
                    else search_document_candidates(project_code, input_value)
                )
                # External requests and inference can outlive source or audience grants.
                current = self.resolve_visibility_context(project_id, user, channel)
                if current is None or not current[1].project_member:
                    continue
                scoped_project, scoped_context = current
                if candidates is not None:
                    external_candidates[project_id] = candidates
                vector_candidates[project_id] = search_board_documents(
                    SimpleNamespace(internal_bot=self._get_service(InternalBotService)),
                    self.repo.card,
                    scoped_project,
                    input_value,
                    context=scoped_context,
                    request=semantic_request,
                )
                semantic_matches.update(
                    self._get_service(CardCommentCompactionService).search_metadata_candidates(
                        user, scoped_project, scoped_context, input_value, channel=channel, request=semantic_request
                    )
                )
        if project is None:
            contexts = self.resolve_work_visibility_contexts(user, channel)
            context = next(iter(contexts.values()), None)
        else:
            current = self.resolve_visibility_context(project, user, channel)
            context = current[1] if current else None
            if paginated and not self.resolve_work_visibility_contexts(user, channel, project_ids={int(project.id)}):
                context = None
        if context is None or not context.active or not context.project_member:
            return {"cards": [], "total_count": 0, "next_fields": None} if paginated else []
        # All board providers have completed. Revalidate source identities locally
        # before source-only candidates influence SQL counts/pages or projections.
        from ....tasks.docling.CompactionMetadataIndexTask import validate_metadata_hits

        final_scopes = contexts if project is None else {int(project.id): context}
        external_matches = {}
        for project_id, scoped_context in final_scopes.items():
            external_matches.update(
                self.repo.card.validate_document_candidates(
                    project_id, external_candidates.get(project_id, []), context=scoped_context
                )
            )
        compactions = self._get_service(CardCommentCompactionService)
        facade = SimpleNamespace(
            card_comment_compaction=compactions, card=self, internal_bot=self._get_service(InternalBotService)
        )
        for card_id, matched in list(semantic_matches.items()):
            with DbSession.use(readonly=False) as db:
                current_card = db.exec(
                    SqlBuilder.select.table(Card).where(Card.id == card_id).execution_options(populate_existing=True)
                ).first()
                scoped_project = (
                    db.exec(
                        SqlBuilder.select.table(Project)
                        .where(Project.id == current_card.project_id)
                        .execution_options(populate_existing=True)
                    ).first()
                    if current_card
                    else None
                )
            if scoped_project is None or int(scoped_project.id) not in final_scopes:
                del semantic_matches[card_id]
                continue
            try:
                fresh = compactions.get_search_metadata(user, scoped_project, current_card, channel=channel)
                similarity = validate_metadata_hits(
                    facade, user, scoped_project, current_card, matched.get("similarity", []), channel=channel
                )
                # Rejected hits may reflect a source change after lexical projection.
                if len(similarity) < len(matched.get("similarity", [])):
                    fresh = compactions.get_search_metadata(user, scoped_project, current_card, channel=channel)
                terms = [item["value"] for item in fresh["keywords"]]
                terms.extend(term for item in fresh["entities"] for term in [item["name"], *item.get("aliases", [])])
                if similarity or any(input_value.casefold() in term.casefold() for term in terms):
                    semantic_matches[card_id] = {**fresh, **({"similarity": similarity} if similarity else {})}
                else:
                    del semantic_matches[card_id]
            except CommentCompactionError:
                del semantic_matches[card_id]
        # Metadata validation can itself outlive grants. Refresh again before
        # attachment vectors influence the final count, page or snippets.
        if project is None:
            contexts = self.resolve_work_visibility_contexts(user, channel)
            context = next(iter(contexts.values()), None)
            final_scopes = contexts
        else:
            current = self.resolve_visibility_context(project, user, channel)
            context = current[1] if current else None
            if paginated and not self.resolve_work_visibility_contexts(user, channel, project_ids={int(project.id)}):
                context = None
            final_scopes = {int(project.id): context} if context else {}
        if context is None or not context.active or not context.project_member:
            return {"cards": [], "total_count": 0, "next_fields": None} if paginated else []
        vector_matches = {}
        for project_id, scoped_context in final_scopes.items():
            vector_matches.update(
                validate_board_document_hits(
                    SimpleNamespace(internal_bot=self._get_service(InternalBotService)),
                    self.repo.card,
                    project_id,
                    vector_candidates.get(project_id, []),
                    context=scoped_context,
                )
            )
        cards = []
        records = self.repo.card.search_context_by_project(
            project,
            input_value,
            date_field=date_field,
            since=since,
            until=until,
            include_closed=include_closed,
            workflow_stages=workflow_stages,
            visibility=visibility,
            created_by=created_by,
            assigned_to=assigned_to,
            limit=limit,
            before_updated_at=before_updated_at,
            before_card=before_card,
            paginated=paginated,
            contexts=contexts,
            context=context,
            **(
                {"additional_card_ids": list(dict.fromkeys([*external_matches, *semantic_matches, *vector_matches]))}
                if external_matches or semantic_matches or vector_matches
                else {}
            ),
        )
        total_count = 0
        next_fields = None
        if paginated:
            records, total_count = records
            if len(records) > limit:
                last = records[limit - 1][0]
                next_fields = (last.updated_at.isoformat(), last.get_uid())
            records = records[:limit]
        document_matches = {}
        for project_id, scoped_context in (contexts or {int(project.id): context}).items():
            document_matches.update(
                self.repo.card.search_document_matches(
                    project_id,
                    [card.id for card, _ in records if int(card.project_id) == project_id],
                    input_value,
                    context=scoped_context,
                )
            )
        for card, _ in records:
            if card.id not in external_matches:
                continue
            matches = document_matches.setdefault(card.id, [])
            existing = {match["attachment_uid"] for match in matches}
            matches.extend(
                match for match in external_matches.get(card.id, []) if match["attachment_uid"] not in existing
            )
            document_matches[card.id] = matches[:2]
        for card, _ in records:
            vector = vector_matches.get(card.id, [])
            if vector:
                existing = {match["attachment_uid"] for match in vector}
                document_matches[card.id] = (
                    vector
                    + [match for match in document_matches.get(card.id, []) if match["attachment_uid"] not in existing]
                )[:2]
        states = self.get_work_states([card for card, _ in records], context=context) if include_work_state else {}
        for card, column in records:
            description = card.description.content
            if len(description) > self.CONTEXT_DESCRIPTION_MAX_LENGTH:
                description = f"{description[: self.CONTEXT_DESCRIPTION_MAX_LENGTH - 3]}..."
            cards.append(
                {
                    "uid": card.get_uid(),
                    "project_uid": SnowflakeID(card.project_id).to_short_code(),
                    "visibility": card.visibility,
                    "title": card.title,
                    "description": description if include_work_state else {"content": description},
                    "project_column_name": column.name,
                    **({"search_metadata": semantic_matches[card.id]} if card.id in semantic_matches else {}),
                    **({"document_matches": document_matches[card.id]} if card.id in document_matches else {}),
                    **(
                        {"project_column_uid": column.get_uid(), "work_state": states[card.id]}
                        if include_work_state
                        else {}
                    ),
                }
            )
        return {"cards": cards, "total_count": total_count, "next_fields": next_fields} if paginated else cards

    def get_my_work_context(
        self,
        user: User,
        mentioned_card_ids: list[int],
        project_uid: str | None = None,
        group: MyWorkGroup | None = None,
        timezone_name: str = "UTC",
        cursor: str | None = None,
        limit: int = 50,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        contexts: dict[int, CardVisibilityContext] | None = None,
    ) -> dict[str, Any]:
        """Summary-only dashboard population, with counts independent of the selected page/group."""
        groups = get_args(MyWorkGroup)
        if group is not None and group not in groups:
            raise ValueError("Invalid My Work group")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        try:
            if not isinstance(timezone_name, str) or len(timezone_name) > 64:
                raise ValueError
            zone = ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid My Work timezone") from exc
        if contexts is None:
            contexts = self.resolve_work_visibility_contexts(user, channel)
        readable = {SnowflakeID(project_id).to_short_code() for project_id in contexts}
        if project_uid is not None:
            if project_uid not in readable:
                raise ValueError("Project not found or not readable")
            readable = {project_uid}
        now = SafeDateTime.now().astimezone(timezone.utc)
        as_of = now
        before = None
        cursor_context = [str(user.id), project_uid, group, timezone_name]
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 1024:
                    raise ValueError
                fields = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if not isinstance(fields, dict) or fields.get("context") != cursor_context:
                    raise ValueError
                as_of = SafeDateTime.fromisoformat(fields["at"])
                values = fields["before"]
                if (
                    not isinstance(values, list)
                    or len(values) != 3
                    or not all(isinstance(value, str) for value in values)
                ):
                    raise ValueError
                before = (SafeDateTime.fromisoformat(values[0]), int(values[1]), int(values[2]))
                if (
                    as_of.utcoffset() is None
                    or before[0].utcoffset() is None
                    or not all(1 <= value <= SnowflakeID.MAX_VALUE for value in before[1:])
                ):
                    raise ValueError
                if as_of > now + timedelta(minutes=5) or now - as_of > timedelta(days=2):
                    raise ValueError
            except (ValueError, TypeError, KeyError, Base64Error) as exc:
                raise ValueError("Invalid My Work cursor") from exc
        local_start = as_of.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = local_start.astimezone(timezone.utc)
        day_end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        upcoming_end = (local_start + timedelta(days=8)).astimezone(timezone.utc)
        records, counts = self.repo.card.get_my_work_context_page(
            user,
            sorted(readable),
            mentioned_card_ids,
            day_start,
            day_end,
            upcoming_end,
            group,
            limit + 1,
            before,
            contexts=contexts,
        )
        page = records[:limit]
        cards = []
        for card, project, column, assigned, mentioned, card_group in page:
            reasons = (["assigned"] if assigned else []) + (["mentioned"] if mentioned else [])
            if card.created_by_user_id == user.id:
                reasons.append("created")
            if card_group in ("overdue", "today", "upcoming"):
                reasons.append("overdue" if card_group == "overdue" else "due_soon")
            cards.append(
                {
                    "uid": card.get_uid(),
                    "title": card.title,
                    "project_uid": project.get_uid(),
                    "project_title": project.title,
                    "project_column_name": column.name,
                    "deadline_at": card.deadline_at.isoformat() if card.deadline_at else None,
                    "updated_at": card.updated_at.isoformat(),
                    "reasons": reasons,
                    "group": card_group,
                    "work_state": {"workflow_stage": column.workflow_stage},
                }
            )
        next_cursor = None
        if len(records) > limit:
            card, project, *_ = page[-1]
            payload = {
                "context": cursor_context,
                "at": as_of.isoformat(),
                "before": [card.updated_at.isoformat(), str(project.id), str(card.id)],
            }
            next_cursor = (
                base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
            )
        return {
            "cards": cards,
            "counts": {key: counts.get(key, 0) for key in groups},
            "total": sum(counts.values()),
            "next_cursor": next_cursor,
            "group": group,
            "project_uid": project_uid,
            "timezone": timezone_name,
            "as_of": as_of.isoformat(),
            "day_start": day_start.isoformat(),
            "day_end": day_end.isoformat(),
            "upcoming_end": upcoming_end.isoformat(),
        }

    def get_my_work_cards(
        self,
        user: User,
        projects: list[dict[str, Any]],
        purposes: set[str],
        mentioned_card_ids: list[int],
        due_before: SafeDateTime,
        date_field: str,
        since: SafeDateTime | None,
        until: SafeDateTime | None,
        limit: int,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        contexts: dict[int, CardVisibilityContext] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a token-efficient cross-project work queue with match reasons."""

        if contexts is None:
            contexts = self.resolve_work_visibility_contexts(user, channel)
        records = self.repo.card.get_my_work_page(
            user,
            [project["uid"] for project in projects],
            purposes,
            mentioned_card_ids,
            SafeDateTime.now(),
            due_before,
            date_field,
            since,
            until,
            limit,
            contexts=contexts,
        )
        cards: list[dict[str, Any]] = []
        work_states = self.get_work_states([card for card, *_ in records], context=next(iter(contexts.values()), None))
        for card, project, column, is_assigned in records:
            reasons = []
            if is_assigned:
                reasons.append("assigned")
            if card.deadline_at is not None and card.deadline_at <= SafeDateTime.now():
                reasons.append("overdue")
            elif card.deadline_at is not None and card.deadline_at <= due_before:
                reasons.append("due_soon")
            if card.created_by_user_id == user.id:
                reasons.append("created")
            if card.id in mentioned_card_ids:
                reasons.append("mentioned")
            cards.append(
                {
                    "uid": card.get_uid(),
                    "title": card.title,
                    "project_uid": project.get_uid(),
                    "project_title": project.title,
                    "project_column_name": column.name,
                    "deadline_at": card.deadline_at.isoformat() if card.deadline_at else None,
                    "updated_at": card.updated_at.isoformat(),
                    "reasons": reasons,
                    "work_state": work_states[card.id],
                }
            )
        return cards

    def get_schedule_workload(
        self,
        user,
        start: str,
        end: str,
        scope: str = "personal",
        project_uid: str | None = None,
        *,
        channel=CollaborationChannel.Api,
    ):
        """Current workload and range-clipped all-worker time on currently readable native cards."""
        from ..CardScheduleCalendar import schedule_range

        start_at, end_at = schedule_range(start, end)
        if scope not in {"board", "personal"} or (scope == "board") != (project_uid is not None):
            raise ValueError("Only board scope requires project_uid")
        as_of = SafeDateTime.now()
        contexts = self.resolve_work_visibility_contexts(user, channel)
        if scope == "board":
            contexts = {pid: ctx for pid, ctx in contexts.items() if SnowflakeID(pid).to_short_code() == project_uid}
            if not contexts:
                raise PermissionError("Project not found or not readable")
        totals = self.repo.card.get_schedule_workload(
            user, start_at, end_at, as_of, contexts=contexts, personal=scope == "personal"
        )
        return {
            "scope": scope,
            "project_uid": project_uid,
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "as_of": as_of.isoformat(),
            "time_attribution": "all_work_on_selected_cards",
            "time_includes_currently_completed_and_archived_cards": True,
            "workload": {"open_count": totals["open_count"], "incomplete_count": totals["incomplete_count"]},
            "time": {key: value for key, value in totals.items() if key not in {"open_count", "incomplete_count"}},
        }

    def list_schedule(
        self,
        user,
        start: str,
        end: str,
        scope: str = "personal",
        project_uid: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        *,
        channel=CollaborationChannel.Api,
        export: bool = False,
    ):
        """Deadline calendar independent of the seven-day My Work queue."""
        from ..CardScheduleCalendar import schedule_cursor, schedule_key, schedule_range

        start_at, end_at = schedule_range(start, end)
        if scope not in {"board", "personal"} or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid Schedule scope or page size")
        if (scope == "board") != (project_uid is not None):
            raise ValueError("Only board scope requires project_uid")
        contexts = self.resolve_work_visibility_contexts(user, channel)
        if scope == "board":
            contexts = {pid: ctx for pid, ctx in contexts.items() if SnowflakeID(pid).to_short_code() == project_uid}
            if not contexts:
                raise PermissionError("Project not found or not readable")
        key = schedule_key(user.id, scope, project_uid, start_at, end_at)
        after = schedule_cursor(key, cursor=cursor) if cursor is not None else None
        if export and cursor is not None:
            raise ValueError("Calendar export requires the complete range")
        count = 10000 if export else limit
        records = self.repo.card.get_schedule_page(
            user, start_at, end_at, count + 1, contexts=contexts, personal=scope == "personal", after=after
        )
        if export and len(records) > count:
            raise ValueError("Calendar exceeds 10000 events; select a smaller range")
        page = records[:count]
        states = {}
        for project_id, context in contexts.items():
            cards = [card for card, *_ in page if card.project_id == project_id]
            states.update(self.get_work_states(cards, context=context))
        items = [
            {
                "card_uid": card.get_uid(),
                "title": card.title,
                "project_uid": project.get_uid(),
                "project_title": project.title,
                "column_uid": column.get_uid(),
                "column_name": column.name,
                "deadline_at": (
                    card.deadline_at if card.deadline_at.tzinfo else card.deadline_at.replace(tzinfo=timezone.utc)
                )
                .astimezone(timezone.utc)
                .isoformat(),
                "work_state": states[card.id],
            }
            for card, project, column in page
        ]
        next_cursor = None
        if len(records) > count:
            card, project, _ = page[-1]
            next_cursor = schedule_cursor(key, [items[-1]["deadline_at"], int(project.id), int(card.id)])
        return {
            "items": items,
            "next_cursor": next_cursor,
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "scope": scope,
        }

    def list_assigned_work(
        self,
        user: User,
        project_uid: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> dict[str, Any]:
        """Shared REST/MCP assigned-work query with current read grants and keyset pagination."""
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")
        contexts = self.resolve_work_visibility_contexts(user, channel)
        readable = {SnowflakeID(project_id).to_short_code() for project_id in contexts}
        if project_uid is not None:
            if project_uid not in readable:
                raise ValueError("Project not found or not readable")
            readable = {project_uid}
        before = None
        if cursor is not None:
            try:
                if len(cursor) > 512:
                    raise ValueError
                raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                fields = json.loads(raw)
                if not isinstance(fields, list) or len(fields) != 3 or not all(isinstance(v, str) for v in fields):
                    raise ValueError
                timestamp = SafeDateTime.fromisoformat(fields[0])
                if timestamp.utcoffset() is None:
                    raise ValueError
                before = (timestamp, InfraHelper.convert_id(fields[1]), InfraHelper.convert_id(fields[2]))
            except (ValueError, TypeError, Base64Error) as exc:
                raise ValueError("Invalid My Work cursor") from exc
        if not readable:
            return {"items": [], "next_cursor": None}
        records = self.repo.card.get_my_work_page(
            user,
            sorted(readable),
            {"assigned"},
            [],
            SafeDateTime.now(),
            SafeDateTime.now(),
            "updated_at",
            None,
            None,
            limit + 1,
            before=before,
            contexts=contexts,
        )
        has_more = len(records) > limit
        page = records[:limit]
        work_states = self.get_work_states([card for card, *_ in page], context=next(iter(contexts.values()), None))
        items = [
            {
                "card_uid": card.get_uid(),
                "title": card.title,
                "project_uid": project.get_uid(),
                "project_title": project.title,
                "column_uid": column.get_uid(),
                "column_name": column.name,
                "updated_at": card.updated_at.isoformat(),
                "deadline_at": card.deadline_at.isoformat() if card.deadline_at else None,
                "work_state": work_states[card.id],
            }
            for card, project, column, _ in page
        ]
        last = page[-1] if has_more and page else None
        next_fields = (last[0].updated_at.isoformat(), last[1].get_uid(), last[0].get_uid()) if last else None
        next_cursor = (
            base64.urlsafe_b64encode(json.dumps(next_fields, separators=(",", ":")).encode()).decode().rstrip("=")
            if next_fields
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    def get_api_page_by_project(
        self,
        project: TProjectParam | None,
        limit: int,
        before_updated_at: SafeDateTime | None = None,
        before_card: TCardParam | None = None,
        user_or_bot: TUserOrBot | None = None,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        include_closed: bool = False,
        workflow_stages: list[str] | None = None,
        visibility: list[str] | None = None,
        created_by: list[str] | None = None,
        assigned_to: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int, tuple[str, str] | None] | None:
        """Return a bounded newest-updated-first card page and opaque cursor fields."""

        resolved = self.resolve_visibility_context(project, user_or_bot, channel)
        if resolved is None:
            return None
        project, context = resolved
        records = self.repo.card.get_page_by_project(
            project,
            limit,
            before_updated_at,
            before_card,
            context=context,
            include_closed=include_closed,
            workflow_stages=workflow_stages,
            visibility=visibility,
            created_by=created_by,
            assigned_to=assigned_to,
        )
        has_more = len(records) > limit
        page = records[:limit]
        resource_payloads = self._get_linked_resource_payloads(
            user_or_bot,
            project,
            [card for card, _ in page if card.is_linked_resource],
            include_content=False,
            **({"channel": channel} if channel != CollaborationChannel.Api else {}),
        )
        work_states = self.get_work_states([card for card, _ in page], context=context)
        cards: list[dict[str, Any]] = []
        for card, column in page:
            api_card = card.api_response()
            api_card["project_column_name"] = column.name
            api_card["work_state"] = work_states[card.id]
            if card.is_linked_resource:
                api_card["linked_resource"] = resource_payloads[card.get_uid()]
            cards.append(api_card)
        next_fields = None
        if has_more and page:
            last_card = page[-1][0]
            next_fields = (last_card.updated_at.isoformat(), last_card.get_uid())
        return (
            cards,
            self.repo.card.count_by_project(
                project,
                context=context,
                include_closed=include_closed,
                workflow_stages=workflow_stages,
                visibility=visibility,
                created_by=created_by,
                assigned_to=assigned_to,
            ),
            next_fields,
        )

    def get_api_list_by_column(self, column: TColumnParam | None) -> list[dict[str, Any]]:
        column = InfraHelper.get_by_id_like(ProjectColumn, column)
        if not column:
            return []

        records = self.repo.card.get_all_by_column(column)
        return [card.api_response() for card in records]

    @overload
    def get_api_assigned_user_list(
        self,
        card: TCardParam | None,
        only_uids: Literal[False] = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...
    @overload
    def get_api_assigned_user_list(
        self,
        card: TCardParam | None,
        only_uids: Literal[True],
        limit: int | None = None,
    ) -> list[str]: ...
    def get_api_assigned_user_list(
        self,
        card: TCardParam | None,
        only_uids: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]] | list[str]:
        """Return assigned users, optionally enforcing a repository row limit."""

        card = InfraHelper.get_by_id_like(Card, card)
        if not card:
            return []

        raw_users = self.repo.card_assigned_user.get_all_by_card(card, only_ids=only_uids, limit=limit)
        if only_uids:
            users = [cast(SnowflakeID, user).to_short_code() for user, _ in raw_users]
        else:
            users = [cast(User, user).api_response() for user, _ in raw_users]
        return users

    def get_api_bot_scope_list(
        self,
        project: TProjectParam | None,
        card: TCardParam | None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return card bot scopes, optionally enforcing a database row limit."""

        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return []
        project, card = params

        scopes = BotScopeHelper.get_list(CardBotScope, limit=limit, card_id=card.id)
        return [scope.api_response() for scope in scopes]

    def get_api_bot_schedule_list(
        self,
        project: TProjectParam | None,
        card: TCardParam | None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return card bot schedules, optionally enforcing a database row limit."""

        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return []
        project, card = params

        pagination = TimeBasedPagination(page=1, limit=limit) if limit is not None else None
        schedules = BotScheduleHelper.get_all_by_scope(CardBotSchedule, None, card, as_api=True, pagination=pagination)

        return schedules

    COMPLETION_CHECKLIST_TITLE = ""

    def _get_completion_checklist(self, card: Card) -> Checklist | None:
        """Return the hidden system checklist backing a card's completion checkbox."""

        checklists = self.repo.checklist.get_all_by_card(card, limit=1, is_system=True)
        return checklists[0] if checklists else None

    def is_check_card(self, card: Card, *, metadata_only: bool = False) -> bool:
        """A deadline card can use its own checkbox when it has no user checklist."""

        if metadata_only:
            if not card.deadline_at:
                from ....infrastructure.repositories.factory.CardRepository import _editor_search_text

                # Preserve Python str.strip(), including Unicode whitespace. Only
                # the eligibility boolean crosses the DB boundary, never the body.
                whitespace = (
                    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
                    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                    "\u2028\u2029\u202f\u205f\u3000"
                )
                dialect = DbEngine.get_main_engine().dialect.name
                content = _editor_search_text(Card.column("description"), dialect)
                if dialect == "postgresql":
                    stripped = func.btrim(content, whitespace)
                elif dialect == "sqlite":
                    stripped = func.trim(content, whitespace)
                else:
                    stripped = func.regexp_replace(content, f"^[{whitespace}]+|[{whitespace}]+$", "")
                with DbSession.use(readonly=False) as db:
                    has_description = db.exec(
                        SqlBuilder.select.column(stripped != "").where(Card.id == card.id)
                    ).first()
                if has_description:
                    return False
        elif card.description.content.strip() and not card.deadline_at:
            return False
        return not self.repo.checklist.get_all_by_card(card, limit=1, is_system=False)

    def ensure_completion_checklist(self, card: Card, completed: bool = False) -> Checklist:
        """Create the hidden completion checklist silently when it does not exist yet."""

        existing = self._get_completion_checklist(card)
        if existing:
            return existing

        checklist = Checklist(
            card_id=card.id,
            title=self.COMPLETION_CHECKLIST_TITLE,
            order=self.repo.checklist.get_next_order(card),
            is_system=True,
            is_checked=completed,
        )
        checkitem = Checkitem(checklist_id=checklist.id, title=card.title, is_checked=completed)
        try:
            self.repo.checklist.insert_completion(checklist, checkitem)
        except IntegrityError:
            existing = self._get_completion_checklist(card)
            if existing:
                return existing
            raise
        return checklist

    def remove_completion_checklist(self, card: Card) -> None:
        """Drop the hidden completion checklist without leaving user-visible traces."""

        existing = self._get_completion_checklist(card)
        if existing:
            self.repo.checklist.delete(existing)

    def sync_completion_checkitem_title(self, card: Card) -> None:
        """Keep the hidden completion item's text aligned with the card title."""

        checklist = self._get_completion_checklist(card)
        if not checklist:
            return
        for checkitem, _, _ in self.repo.checkitem.get_all_by_checklist(checklist):
            checkitem.title = card.title
            self.repo.checkitem.update(checkitem)

    @guard_card_app_mutation
    def set_card_completed(
        self, user_or_bot: TUserOrBot, project: TProjectParam, card: TCardParam, completed: bool
    ) -> bool | None:
        """Toggle a check card's completion state, creating the backing checklist lazily."""

        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
            return None

        if not self.is_check_card(card):
            return False

        checklist = self.ensure_completion_checklist(card, completed=completed)
        checkitems = self.repo.checkitem.get_all_by_checklist(checklist)
        if not checkitems:
            return False

        checkitem = checkitems[0][0]
        if checkitem.is_checked != completed:
            checkitem_service = self._get_service(CheckitemService)
            checkitem_service.toggle_checked(user_or_bot, project, card, checkitem, desired_checked=completed)
            checklist.is_checked = completed
            self.repo.checklist.update(checklist)
        return True

    def _apply_title_labels(self, user_or_bot: TUserOrBot, project: Project, card: Card, title: str) -> str:
        if not title.startswith("["):
            return title
        try:
            plan = parse_title_labels(title, self._get_service(GlobalLabelService).title_names())
            if not plan.global_label_uids:
                return title
            with DbSession.atomic() as db, db.savepoint():
                # Cached names are hints only; recheck current definitions before stripping.
                if db.exec(select(Project.id).where(Project.id == project.id).with_for_update()).first() is None:
                    return title
                current_names = global_label_names(self._get_service(GlobalLabelService).get_api_list())
                if parse_title_labels(title, current_names) != plan:
                    return title
                existing = self.repo.project_label.get_all_by_card(card)
                selected_ids = {label.id for label in existing}
                changed = False
                for uid in plan.global_label_uids:
                    result = self._get_service(ProjectLabelService).use_global(
                        user_or_bot, project, uid, reuse_local=False
                    )
                    if result is None:
                        raise ValueError("Global label unavailable")
                    label_id = InfraHelper.convert_id(result["label"]["uid"])
                    if label_id not in selected_ids:
                        db.insert(CardAssignedProjectLabel(card_id=card.id, project_label_id=label_id))
                        selected_ids.add(label_id)
                        changed = True
                if changed:
                    labels = self.repo.project_label.get_all_by_card(card)
                    db.after_commit(lambda: CardPublisher.labels_updated(project, card, labels))
            return plan.title
        except Exception as error:
            logging.getLogger(__name__).warning("Title label conversion skipped: %s", type(error).__name__)
            return title

    def default_creation_visibility(
        self, project: Project, actor: TUserOrBot, *, app_owned: bool = False
    ) -> CardVisibility:
        """Personal owners use private; authorized app creation uses native board scope."""
        if not isinstance(actor, User):
            return CardVisibility.Internal
        resolved = self.resolve_visibility_context(project, actor)
        if resolved is None or not resolved[1].project_member:
            return CardVisibility.Internal
        project, context = resolved
        if project.owner_id == actor.id and not app_owned:
            with DbSession.use(readonly=False) as db:
                other = db.exec(
                    SqlBuilder.select.table(User)
                    .join(ProjectAssignedUser, ProjectAssignedUser.user_id == User.id)
                    .where(ProjectAssignedUser.project_id == project.id)
                    .where(User.id != actor.id)
                    .where(User.deleted_at.is_(None))
                    .where(User.activated_at.is_not(None))
                    .limit(1)
                ).first()
            if other is None:
                return CardVisibility.Private
        if Env.CARD_INTERNAL_ACCESS_MODE in {"scim", "oidc_issuers"} and context.internal_member is False:
            return CardVisibility.Shared
        return CardVisibility.Internal

    def create(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        column: TColumnParam | None,
        title: str,
        description: EditorContentModel | None = None,
        assign_user_uids: list[str] | None = None,
        *,
        dispatch_effects: bool = True,
        order_override: int | None = None,
        deadline_at: datetime | None = None,
        app_owned: bool = False,
    ) -> tuple[Card, dict[str, Any]] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectColumn, column))
        if not params:
            return None
        project, column = params
        if column.is_archive:
            return None

        with execution_readiness_uow() as execution:
            visibility = self.default_creation_visibility(project, user_or_bot, app_owned=app_owned)
            if visibility == CardVisibility.Private and assign_user_uids:
                raise PermissionError("Private cards do not support assignees")
            card = Card(
                visibility=visibility.value,
                owner_user_id=user_or_bot.id if visibility == CardVisibility.Private else None,
                created_by_user_id=user_or_bot.id if isinstance(user_or_bot, User) else None,
                created_by_bot_id=user_or_bot.id if isinstance(user_or_bot, Bot) else None,
                project_id=project.id,
                project_column_id=column.id,
                workflow_stage_entered_key=column.workflow_stage,
                workflow_stage_entered_at=SafeDateTime.now() if column.workflow_stage else None,
                title=title,
                description=description or EditorContentModel(),
                deadline_at=deadline_at,
                order=order_override
                if order_override is not None
                else self.repo.card.get_next_order(column, {"project_id": project.id}),
            )
            self.repo.card.insert(card)
            self.mark_card_changed(card, self.UNREAD_TARGET_CARD)
            execution.watch_new(card.id)
            converted_title = self._apply_title_labels(user_or_bot, project, card, title)
            if converted_title != title:
                card.title = converted_title
                self.repo.card.update(card)

            users: list[User] = []
            if assign_user_uids:
                raw_users = self.repo.project_assigned_user.get_all_by_project(project, where_users_in=assign_user_uids)
                for assign_user, project_assigned_user in raw_users:
                    card_assigned_user = CardAssignedUser(
                        project_assigned_id=project_assigned_user.id,
                        card_id=card.id,
                        user_id=assign_user.id,
                    )
                    users.append(assign_user)
                    self.repo.card_assigned_user.insert(card_assigned_user)

            is_check_card = bool(deadline_at) or not card.description.content.strip()
            if is_check_card:
                self.ensure_completion_checklist(card)

            api_card = card.board_api_response(
                0,
                [user.get_uid() for user in users],
                [],
                [label.api_response() for label in self.repo.project_label.get_all_by_card(card)]
                if converted_title != title
                else [],
                creator=self._card_creator_projection(card, user_or_bot)
                if isinstance(user_or_bot, (User, Bot))
                else None,
                completed=False,
                is_check_card=is_check_card,
            )
            model = {"card": api_card}

            if dispatch_effects:
                self.dispatch_created(user_or_bot, project, column, card, model, users)

        return card, api_card

    def dispatch_created(
        self,
        user_or_bot: TUserOrBot,
        project: Project,
        column: ProjectColumn,
        card: Card,
        model: dict[str, Any],
        users: list[User] | None = None,
        *,
        include_bot: bool = True,
        include_notifications: bool = True,
    ) -> None:
        dispatch_after_commit(CardPublisher.created, project, column, model)
        dispatch_after_commit(CardActivityTask.card_created, user_or_bot, project, card)
        if include_bot:
            dispatch_after_commit(CardBotTask.card_created, user_or_bot, project, card)
        if include_notifications and users:
            notification_service = self._get_service(NotificationService)
            for user in users:
                notification_service.notify_assigned_to_card(user_or_bot, user, project, card)

    @guard_card_app_mutation
    def cardify_selection(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        selected_markdown: str,
    ) -> dict[str, Any] | None:
        """Extract the selected body fragment into a child card with a link back.

        Creates a child card in the parent's column, links them with the first
        global parent-child relationship type, replaces the selection with a
        [[title]] link, and returns the child card summary.
        """
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
            return None

        selected_markdown = selected_markdown.strip()
        if not selected_markdown:
            return None

        # Derive a concise title from the first meaningful line
        first_line = next(
            (line.lstrip("#-*> ").strip() for line in selected_markdown.split("\n") if line.strip()),
            "Untitled",
        )
        title = first_line[:80] + ("..." if len(first_line) > 80 else "")

        # Create the child card in the same column
        child = Card(
            visibility=card.visibility,
            owner_user_id=card.owner_user_id,
            created_by_user_id=user_or_bot.id if isinstance(user_or_bot, User) else None,
            created_by_bot_id=user_or_bot.id if isinstance(user_or_bot, Bot) else None,
            project_id=project.id,
            project_column_id=card.project_column_id,
            title=title,
            description=EditorContentModel(content=selected_markdown),
            order=self.repo.card.get_next_order(
                InfraHelper.get_by_id_like(ProjectColumn, card.project_column_id),
                {"project_id": project.id},
            ),
        )
        with execution_readiness_uow() as execution:
            self.repo.card.insert(child)
            execution.watch_new(child.id)

            # A child is contained by its parent; an arbitrary first type could block execution.
            contains_type = self._contains_relationship_type()
            self.repo.card_relationship.insert(
                CardRelationship(
                    card_id_parent=card.id,
                    card_id_child=child.id,
                    relationship_type_id=contains_type.id,
                )
            )

            # Replace the selection with a link in the parent body
            full_markdown = card.description.content or ""
            link = f"[[{title}]]"
            index = full_markdown.find(selected_markdown)
            if index != -1:
                new_markdown = full_markdown[:index] + link + full_markdown[index + len(selected_markdown) :]
                card.description = EditorContentModel(content=new_markdown)
                self.repo.card.update(card)

        CardPublisher.updated(project, card, None, {"description": link})
        CardActivityTask.card_created(user_or_bot, project, child)

        return {
            "child_card_uid": child.get_uid(),
            "child_card_title": title,
            "link_markdown": link,
        }

    @guard_card_app_mutation
    def convert_description_checkboxes(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
    ) -> dict[str, Any] | None:
        """Convert markdown checkboxes in the card body into a native checklist.

        Parses `- [ ]` / `- [x]` lines, creates a native checklist preserving
        completion state, removes the checkbox lines from the description, and
        returns the created checklist summary.
        """
        import re

        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
            return None

        markdown = card.description.content or ""
        pattern = re.compile(r"^[\t ]*(?:[-*+]|\d+\.)\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)
        matches = list(pattern.finditer(markdown))
        if not matches:
            return {"checklist_uid": None, "item_count": 0, "message": "No markdown checkboxes found"}

        # Build checklist items preserving completion state
        items = []
        for match in matches:
            is_checked = match.group(1).lower() == "x"
            title = match.group(2).strip()
            if title:
                items.append({"title": title, "is_checked": is_checked})

        if not items:
            return {"checklist_uid": None, "item_count": 0, "message": "No valid checkbox titles"}

        # Create the native checklist via ChecklistService
        checklist_service = self._get_service_by_name("checklist")
        checklist = checklist_service.create(user_or_bot, project, card.get_uid(), "Converted checklist")
        if not checklist:
            return None

        # Create checkitems with completion state
        checkitem_service = self._get_service_by_name("checkitem")
        for item in items:
            created = checkitem_service.create(user_or_bot, project, card.get_uid(), checklist.get_uid(), item["title"])
            if created and item["is_checked"]:
                created.is_checked = True
                self.repo.checkitem.update(created)

        # Remove checkbox lines from the description
        lines = markdown.split("\n")
        kept = [line for line in lines if not pattern.match(line)]
        new_markdown = "\n".join(kept)
        new_markdown = re.sub(r"\n{3,}", "\n\n", new_markdown).strip()

        card.description = EditorContentModel(content=new_markdown)
        self.repo.card.update(card)
        CardPublisher.updated(project, card, None, {"description": "checkboxes converted"})

        return {
            "checklist_uid": checklist.get_uid(),
            "item_count": len(items),
            "checked_count": sum(1 for item in items if item["is_checked"]),
            "remaining_markdown": new_markdown,
        }

    def get_section_comment_counts(
        self,
        project: TProjectParam | None,
        card: TCardParam | None,
    ) -> list[dict[str, Any]] | None:
        """Return comment counts grouped by section_anchor for the gutter UI."""

        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params

        raw_comments = self.repo.card_comment.get_all_by_card(card)
        counts: dict[str, int] = {}
        for comment, _ in raw_comments:
            anchor_value = getattr(comment, "section_anchor", None)
            if anchor_value:
                counts[anchor_value] = counts.get(anchor_value, 0) + 1

        return [{"anchor": anchor_value, "count": count} for anchor_value, count in sorted(counts.items())]

    @guard_card_app_mutation
    def copy_selection_to_wiki(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        selected_markdown: str,
        wiki_title: str | None = None,
    ) -> dict[str, Any] | None:
        """Copy the selected body fragment into a new project wiki.

        The original card body is preserved unchanged; no relationship or link
        is created between the card and the wiki.
        """
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
            return None

        selected_markdown = selected_markdown.strip()
        if not selected_markdown:
            return None

        # Derive title from the first meaningful line or use the provided one
        if not wiki_title:
            wiki_title = next(
                (line.lstrip("#-*> ").strip() for line in selected_markdown.split("\n") if line.strip()),
                "Untitled wiki",
            )
            wiki_title = wiki_title[:300]

        wiki_service = self._get_service_by_name("project_wiki")
        result = wiki_service.create(user_or_bot, project, wiki_title, EditorContentModel(content=selected_markdown))
        if not result:
            return None

        wiki, api_wiki = result
        return {
            "wiki_uid": wiki.get_uid(),
            "wiki_title": wiki_title,
            "card_body_unchanged": True,
        }

    def get_change_feed(
        self,
        project: TProjectParam | None,
        limit: int = 50,
        before_activity_uid: str | None = None,
        *,
        user: TUserOrBot | None = None,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> dict[str, Any] | None:
        """Expose only currently readable card changes with a visible keyset cursor."""
        resolved = self.resolve_visibility_context(project, user, channel)
        if resolved is None:
            return None
        project, context = resolved
        limit = max(1, min(limit, 100))
        card_activities = self.repo.activity.get_card_change_page(
            project,
            limit,
            context=context,
            before_activity_uid=before_activity_uid,
        )

        has_more = len(card_activities) > limit
        page = card_activities[:limit]

        entries = []
        for activity in page:
            entry = {
                "activity_uid": activity.get_uid(),
                "activity_type": activity.activity_type.value
                if hasattr(activity.activity_type, "value")
                else str(activity.activity_type),
                "card_uid": activity.card_id.to_short_code() if activity.card_id else None,
                "project_uid": project.get_uid(),
                "created_at": str(activity.created_at),
            }
            if hasattr(activity, "user_id") and activity.user_id:
                entry["actor_user_uid"] = activity.user_id.to_short_code()
            elif hasattr(activity, "bot_id") and activity.bot_id:
                entry["actor_bot_uid"] = activity.bot_id.to_short_code()
            entries.append(entry)

        next_cursor = page[-1].get_uid() if has_more and page else None
        return {"entries": entries, "has_more": has_more, "next_cursor": next_cursor}

    def _invalidate_source_card_mirrors(self, card: Card) -> None:
        if not card.is_linked_resource:
            self._invalidate_source_mirrors(source_uid=card.get_uid())

    def invalidate_source_project_mirrors(self, project: Project) -> None:
        """Refresh native references when their source board's access changes."""
        self._invalidate_source_mirrors(source_project=project)

    def _invalidate_source_mirrors(
        self, *, source_uid: str | None = None, source_project: Project | None = None
    ) -> None:
        """Page source-board IDs and matching mirrors without a global reference scan."""
        with DbSession.atomic() as db:
            if source_uid is not None:
                self._queue_source_mirror_invalidations(db, [source_uid])
                return
            if source_project is None:
                return
            last_source_id = 0
            while True:
                source_ids = db.exec(
                    SqlBuilder.select.column(Card.id)
                    .where(Card.project_id == source_project.id, Card.id > last_source_id)
                    .order_by(Card.id)
                    .limit(self.MIRROR_INVALIDATION_BATCH_SIZE)
                ).all()
                if not source_ids:
                    return
                self._queue_source_mirror_invalidations(db, [source_id.to_short_code() for source_id in source_ids])
                if len(source_ids) < self.MIRROR_INVALIDATION_BATCH_SIZE:
                    return
                last_source_id = source_ids[-1]

    def _queue_source_mirror_invalidations(self, db: DbSession, source_uids: list[str]) -> None:
        """Keep query pages bounded; callbacks retain only affected destination rows."""
        last_mirror_id = 0
        while True:
            references = db.exec(
                select(Card, Project)
                .join(Project, Project.id == Card.project_id)
                .where(
                    Card.source_type == Card.LINKED_RESOURCE_PROJECT_CARD,
                    Card.source_uid.in_(source_uids),
                    Card.id > last_mirror_id,
                    Card.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                )
                .order_by(Card.id)
                .limit(self.MIRROR_INVALIDATION_BATCH_SIZE)
            ).all()
            for mirror, destination in references:
                db.after_commit(
                    lambda destination=destination, mirror=mirror: CardPublisher.linked_resource_changed(
                        destination, mirror
                    )
                )
            if len(references) < self.MIRROR_INVALIDATION_BATCH_SIZE:
                return
            last_mirror_id = references[-1][0].id

    @guard_card_app_mutation
    def change_visibility(
        self,
        user: User,
        project: TProjectParam,
        card: TCardParam,
        target: CardVisibility,
        *,
        channel: CollaborationChannel,
        confirmed: bool,
        expected_visibility: str,
    ) -> dict[str, Any] | None:
        """Lock, revalidate authority, and audit a disclosure transition atomically."""
        with DbSession.atomic() as db:
            current = db.exec(
                SqlBuilder.select.table(Card).where(Card.id == InfraHelper.convert_id(card)).with_for_update()
            ).first()
            resolved = self.resolve_readable_card(project, current, user, channel)
            if resolved is None:
                return None
            project, current, context = resolved
            if current.source_type == Card.LINKED_RESOURCE_PROJECT_CARD:
                return None
            actor = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)).first()
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.user_id == user.id,
                    ProjectRole.project_id == project.id,
                )
            ).first()
            roles = ["*"] if actor and actor.is_admin else (role.actions if role else [])
            context = replace(context, can_update_card="*" in roles or ProjectRoleAction.CardUpdate.value in roles)
            if not context.can_change_visibility(CardVisibility(current.visibility), target, confirmed=confirmed):
                raise PermissionError("Card visibility transition is not allowed")
            if current.visibility != expected_visibility:
                raise ValueError("Card visibility changed; reload before confirming")
            if current.visibility == target.value:
                return {"visibility": current.visibility, "updated_at": current.updated_at.isoformat()}
            db.insert(
                CardVisibilityChange(
                    card_id=current.id,
                    changed_by_user_id=user.id,
                    channel=channel.value,
                    previous_visibility=current.visibility,
                    next_visibility=target.value,
                )
            )
            current.visibility = target.value
            db.update(current)
            self._invalidate_source_card_mirrors(current)
            # No card identifiers or content in the board-wide invalidation.
            from ....publishers import ProjectPublisher

            db.after_commit(lambda: ProjectPublisher.updated(project, {"card_visibility_changed": True}))
            return {"visibility": current.visibility, "updated_at": current.updated_at.isoformat()}

    @guard_card_app_mutation
    def update(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        form: dict[str, Any],
        *,
        expected_description: str | None = None,
    ) -> dict[str, Any] | Literal[True] | None:
        with DbSession.atomic():
            return self._update(user_or_bot, project, card, form, expected_description=expected_description)

    def _update(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        form: dict[str, Any],
        *,
        expected_description: str | None = None,
    ) -> dict[str, Any] | Literal[True] | None:
        """Update a card, optionally guarding a description-only edit against concurrent writes."""

        if expected_description is not None and set(form) != {"description"}:
            raise ValueError("Conditional description updates cannot change other card fields")
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if card.is_linked_resource:
            return None

        title_normalized = False
        if isinstance(form.get("title"), str):
            converted_title = self._apply_title_labels(user_or_bot, project, card, form["title"])
            title_normalized = converted_title != form["title"]
            form = {**form, "title": converted_title}

        validators: TMutableValidatorMap = {
            "title": "not_empty",
            "deadline_at": "nullable",
            "description": "default",
        }
        old_record = self.apply_mutates(card, form, validators)
        if not old_record:
            # Return persisted normalization so API/MCP do not echo stripped tokens.
            if title_normalized:
                return {"title": card.title}
            return True

        checkitem_cardified_from = None
        if "title" in old_record:
            checkitem_cardified_from = InfraHelper.get_by(Checkitem, "cardified_id", card.id)
            if checkitem_cardified_from:
                checkitem_cardified_from.title = card.title
                self.repo.checkitem.update(checkitem_cardified_from)
            self.sync_completion_checkitem_title(card)

        target_type = self.UNREAD_TARGET_DESCRIPTION if "description" in old_record else self.UNREAD_TARGET_CARD
        if expected_description is None:
            self.repo.card.update(card)
        elif not self.repo.card.update_description_if_current(card, expected_description):
            raise CardDescriptionConflict("Card description changed after review: concurrent update")
        self.mark_card_changed(card, target_type)

        if "description" in old_record or "deadline_at" in old_record:
            if self.is_check_card(card):
                self.ensure_completion_checklist(card)
            else:
                self.remove_completion_checklist(card)

        model: dict[str, Any] = {}
        for key in form:
            if key not in validators or key not in old_record:
                continue
            model[key] = convert_python_data(getattr(card, key))

        changed_card = card.model_copy(deep=True)
        changed_item = checkitem_cardified_from.model_copy(deep=True) if checkitem_cardified_from else None

        with DbSession.atomic():
            self._invalidate_source_card_mirrors(card)
            dispatch_after_commit(CardPublisher.updated, project, changed_card, changed_item, model)
            if "description" in model and changed_card.description:
                notification_service = self._get_service(NotificationService)
                notification_service.notify_mentioned_in_card(user_or_bot, project, changed_card)
            dispatch_after_commit(CardActivityTask.card_updated, user_or_bot, project, old_record, changed_card)
            dispatch_after_commit(CardBotTask.card_updated, user_or_bot, project, changed_card)

        return model

    @guard_card_app_mutation
    def change_order(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        order: int,
        new_column: TColumnParam | None,
        *,
        move_descendants: bool | None = None,
        confirmation_revision: str | None = None,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> bool | dict[str, Any] | None:
        move_options = {} if channel == CollaborationChannel.Api else {"channel": channel}
        with DbSession.atomic() as db:
            params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
            if not params:
                return None
            project, card = params
            db.exec(select(Project.id).where(Project.id == project.id).with_for_update()).first()
            card = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.id == card.id, Card.project_id == project.id, Card.deleted_at.is_(None))
                .execution_options(populate_existing=True)
            ).first()
            if card is None:
                return None
            # Caller-supplied columns can predate a binding edit or deletion.
            # Read and lock both policies on the primary under the board fence,
            # retaining the same authoritative target through confirmation and move.
            column_ids = {card.project_column_id}
            target_id = InfraHelper.convert_id(new_column) if new_column else None
            if target_id is not None:
                column_ids.add(target_id)
            columns = {
                column.id: column
                for column in db.exec(
                    SqlBuilder.select.table(ProjectColumn)
                    .where(ProjectColumn.project_id == project.id, ProjectColumn.id.in_(column_ids))
                    .order_by(ProjectColumn.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).all()
            }
            if card.project_column_id not in columns:
                return None
            if target_id is not None:
                new_column = columns.get(target_id)
                if new_column is None:
                    return None
            if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
                target = InfraHelper.get_by_id_like(ProjectColumn, new_column) if new_column else None
                if target is None or not target.is_archive:
                    return None
            family = self._root_move_family(db, project, card, new_column)
            if family:
                descendants, revision = family
                # No count or names: the tree can contain cards the caller cannot read.
                if move_descendants is None or confirmation_revision != revision:
                    return {"confirmation_required": True, "confirmation_revision": revision}
                if move_descendants:
                    actor = (
                        db.exec(
                            select(User.id, User.activated_at, User.deleted_at, User.is_admin).where(
                                User.id == user_or_bot.id
                            )
                        ).first()
                        if isinstance(user_or_bot, User)
                        else None
                    )
                    role = (
                        db.exec(
                            SqlBuilder.select.table(ProjectRole).where(
                                ProjectRole.user_id == user_or_bot.id,
                                ProjectRole.project_id == project.id,
                            )
                        ).first()
                        if actor
                        else None
                    )
                    actions = role.actions if role else []
                    if (
                        actor is None
                        or actor[2]
                        or not actor[1]
                        or not (actor[3] or "*" in actions or ProjectRoleAction.CardUpdate.value in actions)
                    ):
                        raise ValueError("Current card update permission is required for a family move")
                    from ..CardCrossBoardOperations import _grant

                    for child in [card, *descendants]:
                        if child.project_id != project.id or child.is_linked_resource:
                            raise ValueError("The complete card family cannot be moved to this column")
                        resolved = self.resolve_readable_card(project, child, user_or_bot, channel)
                        if resolved is None or not _grant(db, user_or_bot, resolved[0], ProjectRoleAction.Read):
                            raise ValueError("The complete card family cannot be moved to this column")
                        owner = db.exec(
                            SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == child.id)
                        ).first()
                        if owner is not None and owner.app_key is not None:
                            raise ValueError("App-owned cards cannot join a family move")
                        require_card_app_mutation(db, user_or_bot, child, project)
                result = self._change_order(user_or_bot, project, card, order, new_column, **move_options)
                if not result:
                    raise ValueError("Card movement failed")
                if move_descendants:
                    target_id = InfraHelper.convert_id(new_column)
                    for child in descendants:
                        if child.project_column_id == target_id:
                            continue
                        last = db.exec(
                            select(func.max(Card.order)).where(
                                Card.project_column_id == target_id,
                                Card.deleted_at.is_(None),
                            )
                        ).first()
                        last = last[0] if isinstance(last, tuple) else last
                        if not self._change_order(
                            user_or_bot, project, child, (last or 0) + 1, new_column, **move_options
                        ):
                            raise ValueError("Card family movement failed")
                return result
            return self._change_order(user_or_bot, project, card, order, new_column, **move_options)

    def _root_move_family(self, db, project, card, new_column):
        target = InfraHelper.get_by_id_like(ProjectColumn, new_column) if new_column else None
        old = InfraHelper.get_by_id_like(ProjectColumn, card.project_column_id)
        if (
            target is None
            or old is None
            or target.project_id != project.id
            or target.deleted_at is not None
            or target.is_archive
            or not target.workflow_stage
            or old.workflow_stage == target.workflow_stage
        ):
            return None
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition).where(
                WorkflowStageDefinition.key == target.workflow_stage,
            )
        ).first()
        if stage is None or not stage.confirm_descendant_move:
            return None
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition)
            .where(
                WorkflowStageDefinition.id == stage.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if stage is None or not stage.confirm_descendant_move:
            return None

        # The board fence is also held by native relationship writers. Only the
        # system containment semantic counts; references and prerequisites do not.
        def edges_from(parent_ids=None, child_id=None):
            query = (
                SqlBuilder.select.table(CardRelationship)
                .join(
                    GlobalCardRelationshipType,
                    GlobalCardRelationshipType.id == CardRelationship.relationship_type_id,
                )
                .where(
                    GlobalCardRelationshipType.is_system_default.is_(True),
                    GlobalCardRelationshipType.machine_semantic == "contains",
                )
            )
            if parent_ids is not None:
                query = query.where(CardRelationship.card_id_parent.in_(parent_ids))
            if child_id is not None:
                query = query.where(CardRelationship.card_id_child == child_id)
            return db.exec(query).all()

        if edges_from(child_id=card.id):
            return None
        children, descendants = {}, []
        visited, frontier = {card.id}, [card.id]
        while frontier:
            edges = edges_from(parent_ids=frontier)
            next_ids = set()
            for edge in edges:
                children.setdefault(edge.card_id_parent, set()).add(edge.card_id_child)
                if edge.card_id_child not in visited:
                    next_ids.add(edge.card_id_child)
            frontier = []
            visited.update(next_ids)
            rows = (
                db.exec(
                    SqlBuilder.select.table(Card)
                    .where(
                        Card.id.in_(next_ids),
                        Card.deleted_at.is_(None),
                    )
                    .order_by(Card.id)
                ).all()
                if next_ids
                else []
            )
            for child in rows:
                descendants.append(child)
                # Cross-board relationships remain intact, but cannot be moved by
                # a column-local command or used to acquire foreign board locks.
                if child.project_id == project.id:
                    frontier.append(child.id)
        if not descendants:
            return None
        # Fence the user's decision against changed positions, hierarchy or policy.
        payload = (
            [str(stage.edit_revision()), str(target.id)]
            + [f"{row.id}:{row.project_column_id}:{row.order}:{row.updated_at}" for row in [card, *descendants]]
            + [f"{parent}:{child}" for parent in sorted(visited) for child in sorted(children.get(parent, ()))]
        )
        return descendants, sha256("|".join(payload).encode()).hexdigest()

    def _change_order(self, user_or_bot, project, card, order, new_column, *, channel=CollaborationChannel.Api):
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params

        with execution_readiness_uow() as execution:
            # Reorders update neighboring cards too. Take the existing board
            # fence before any card lock to avoid concurrent moves locking
            # different cards and then waiting for each other's neighbors.
            if execution.db.exec(select(Project.id).where(Project.id == project.id).with_for_update()).first() is None:
                return None
            if isinstance(user_or_bot, InternalBot):
                project, user_or_bot, card = require_compactor_card(
                    execution.db,
                    project.id,
                    user_or_bot.id,
                    card.id,
                    action="archive",
                )
                require_card_app_mutation(execution.db, user_or_bot, card, project)
                target = InfraHelper.get_by_id_like(ProjectColumn, new_column) if new_column else None
                if target is None or not target.is_archive:
                    raise ValueError("A compactor can only move cards into the archive")
            card = execution.db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.column("id") == card.id)
                .where(Card.column("project_id") == project.id)
                .with_for_update()
            ).first()
            if card is None or card.deleted_at is not None:
                return None
            if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
                target = InfraHelper.get_by_id_like(ProjectColumn, new_column) if new_column else None
                if target is None or not target.is_archive:
                    return None
            old_column = InfraHelper.get_by_id_like(ProjectColumn, card.project_column_id)
            if not old_column or old_column.project_id != project.id:
                return None
            # REST forms use an empty parent UID for same-column reorders.
            new_column = new_column or None
            if new_column:
                new_column = InfraHelper.get_by_id_like(ProjectColumn, new_column)
                if not new_column or new_column.project_id != project.id or new_column.deleted_at is not None:
                    return None
                if card.is_linked_resource and new_column.is_archive:
                    if not self.can_delete(user_or_bot, card):
                        raise CardDeleteForbidden(
                            "Only the original card author or an administrator can delete this card"
                        )
                    return self._delete_card(user_or_bot, project, card)
                if new_column.id == old_column.id:
                    new_column = None
            DecisionGate(self).require(
                execution.db, user_or_bot, project, card, old_column, new_column, channel=channel
            )
            execution.watch_card_and_dependents(card.id)
            old_order = card.order
            entering_archive = bool(new_column and new_column.is_archive and not card.archived_at)
            if new_column:
                card.project_column_id = new_column.id
                card.archived_at = (card.archived_at or SafeDateTime.now()) if new_column.is_archive else None
            card.order = order
            self.repo.card.update_row_order(
                card, old_column, old_order, order, new_column, preserve_shifted_updated_at=True
            )
            self.repo.card.update(card)
            self._get_service(WorkflowStagePolicyService).apply_transition(
                user_or_bot, project, card, old_column, new_column
            )
            if new_column is not None:
                self.mark_card_changed(card, self.UNREAD_TARGET_CARD)
            if entering_archive:
                from ..AppCardArchive import record_app_card_archive

                record_app_card_archive(execution.db, user_or_bot, project, card)
            if new_column and not card.is_linked_resource and isinstance(user_or_bot, (User, Bot)):
                prepare = vars(CardBotTask.card_moved).get("_workflow_prepare")
                if prepare is not None:
                    prepare(user_or_bot, project, card, old_column, False)
            execution.db.after_commit(
                lambda: self.notify_order_changed(user_or_bot, project, card, old_column, new_column)
            )
        return True

    def notify_order_changed(
        self,
        user_or_bot: TUserOrBot,
        project: Project,
        card: Card,
        old_column: ProjectColumn,
        new_column: ProjectColumn | None,
    ) -> None:
        CardPublisher.order_changed(project, card, old_column, cast(ProjectColumn, new_column))
        if new_column is not None:
            # The move committed before this callback; replica discovery may lag.
            with DbSession.atomic():
                dependency_children = self._dependency_children(card)
            self.publish_work_states(project, [card.id, *dependency_children])

        if isinstance(user_or_bot, InternalBot):
            # Profile effects have WorkflowEffectRun receipts, never User/Bot foreign keys.
            return
        if new_column and not card.is_linked_resource:
            CardBotTask.enqueue_card_moved_webhook(
                user_or_bot, project, card, old_column, cast(ProjectColumn, new_column)
            )
            CardActivityTask.card_moved(user_or_bot, project, card, old_column)
            CardBotTask.card_moved(user_or_bot, project, card, old_column, False)

    @guard_card_app_mutation
    def assign_self(self, user: User, project: TProjectParam, card: TCardParam) -> dict[str, Any]:
        """Assign the authenticated project member additively; never infer an identity."""
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            raise ValueError("Card not found in project")
        project, card = params
        if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
            raise ValueError("Card mirrors are read-only")
        self._reject_private_assignment(card)
        member = self.repo.project_assigned_user.find_by_user_and_project(user, project)
        if member is None:
            raise ValueError("Current user must first be onboarded to this project")
        previous = self.repo.card_assigned_user.get_all_by_card(card, only_ids=True)
        changed = self.repo.card_assigned_user.add_member(card, member)
        if changed:
            users = [assigned for assigned, _ in self.repo.card_assigned_user.get_all_by_card(card)]
            CardPublisher.assigned_users_updated(project, card, users)
            CardActivityTask.card_assigned_users_updated(
                user, project, card, [uid for uid, _ in previous], [item.id for item in users]
            )
        return {"card_uid": card.get_uid(), "assigned_user_uid": user.get_uid(), "changed": changed}

    @guard_card_app_mutation
    def assign_member(
        self, actor: TUserOrBot, project: TProjectParam, card: TCardParam, assignee_uid: str
    ) -> dict[str, Any]:
        """Add an existing active project member; never invite or replace other assignees."""
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            raise LookupError("Card not found in project")
        project, card = params
        if getattr(card, "source_type", None) == Card.LINKED_RESOURCE_PROJECT_CARD:
            raise ValueError("Card mirrors are read-only")
        self._reject_private_assignment(card)
        assignee = InfraHelper.get_by_id_like(User, assignee_uid)
        if assignee is None or assignee.deleted_at is not None or assignee.activated_at is None:
            raise ValueError("Select an active member of this project")
        member = self.repo.project_assigned_user.find_by_user_and_project(assignee, project)
        if member is None:
            raise ValueError("Select an active member of this project")
        previous = self.repo.card_assigned_user.get_all_by_card(card, only_ids=True)
        changed = self.repo.card_assigned_user.add_member(card, member)
        users = [assigned for assigned, _ in self.repo.card_assigned_user.get_all_by_card(card)]
        if changed:
            CardPublisher.assigned_users_updated(project, card, users)
            CardActivityTask.card_assigned_users_updated(
                actor, project, card, [uid for uid, _ in previous], [item.id for item in users]
            )
            self._get_service(NotificationService).notify_assigned_to_card(actor, assignee, project, card)
        return {"changed": changed, "member_uids": [assigned.get_uid() for assigned in users]}

    @guard_card_app_mutation
    @card_change_transaction
    def update_assigned_users(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        assign_user_uids: list[str] | None = None,
    ) -> list[User] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if card.is_linked_resource:
            return None
        self._reject_private_assignment(card)

        old_assigned_users = self.repo.card_assigned_user.get_all_by_card(card, only_ids=True)
        old_assigned_user_ids = [user_id for user_id, _ in old_assigned_users]

        if old_assigned_user_ids:
            self.repo.card_assigned_user.delete_all_by_card(card)

        raw_users = []
        if assign_user_uids:
            raw_users = self.repo.project_assigned_user.get_all_by_project(project, where_users_in=assign_user_uids)

        new_users: list[User] = []
        if raw_users:
            for user, project_assigned_user in raw_users:
                card_assigned_user = CardAssignedUser(
                    project_assigned_id=project_assigned_user.id,
                    card_id=card.id,
                    user_id=user.id,
                )
                self.repo.card_assigned_user.insert(card_assigned_user)
                new_users.append(user)

        dispatch_after_commit(CardPublisher.assigned_users_updated, project, card, new_users)
        self.mark_card_changed(card, self.UNREAD_TARGET_CARD)

        notification_service = self._get_service(NotificationService)
        for user in new_users:
            if user.id in old_assigned_user_ids:
                continue
            notification_service.notify_assigned_to_card(user_or_bot, user, project, card)

        dispatch_after_commit(
            CardActivityTask.card_assigned_users_updated,
            user_or_bot,
            project,
            card,
            old_assigned_user_ids,
            [user.id for user in new_users],
        )
        return new_users

    @guard_card_app_mutation
    @card_change_transaction
    def update_labels(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        labels: Sequence[TProjectLabelParam],
        *,
        dispatch_effects: bool = True,
    ) -> bool | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if card.is_linked_resource:
            return None

        old_labels = self.repo.project_label.get_all_by_card(card)

        self.repo.card_assigned_project_label.delete_all_by_card(card)

        new_labels = self.repo.project_label.get_all_by_project(project, where_in=labels)
        for label in new_labels:
            card_assigned_label = CardAssignedProjectLabel(card_id=card.id, project_label_id=label.id)
            self.repo.card_assigned_project_label.insert(card_assigned_label)

        if dispatch_effects:
            dispatch_after_commit(CardPublisher.labels_updated, project, card, new_labels)
        self.mark_card_changed(card, self.UNREAD_TARGET_CARD)
        if dispatch_effects:
            dispatch_after_commit(
                CardActivityTask.card_labels_updated,
                user_or_bot,
                project,
                card,
                [label.id for label in old_labels],
                [label.id for label in new_labels],
            )
            dispatch_after_commit(CardBotTask.card_labels_updated, user_or_bot, project, card)

        return True

    def archive_automatically(self, project_id, profile_id, card_id, *, rule_id, now=None) -> dict[str, str]:
        """Archive verified work under explicit current profile authority; no model decision."""
        started_at, started_clock = SafeDateTime.now(), monotonic_ns()
        now = now or started_at
        now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
        with DbSession.atomic() as db:
            project, profile, card = require_compactor_card(db, project_id, profile_id, card_id, action="archive")
            require_card_app_mutation(db, profile, card, project)
            if card.archived_at:
                return {"status": "skipped", "reason": "already_archived"}
            column = db.exec(
                SqlBuilder.select.table(ProjectColumn)
                .where(
                    ProjectColumn.id == card.project_column_id,
                    ProjectColumn.project_id == project.id,
                    ProjectColumn.deleted_at.is_(None),
                    ProjectColumn.is_archive.is_(False),
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            ).first()
            stage = (
                db.exec(
                    SqlBuilder.select.table(WorkflowStageDefinition)
                    .where(
                        WorkflowStageDefinition.key == column.workflow_stage,
                    )
                    .execution_options(populate_existing=True)
                    .with_for_update()
                ).first()
                if column
                else None
            )
            if stage is None or not stage.is_active or not stage.counts_as_completed:
                return {"status": "skipped", "reason": "policy_changed"}
            from .WorkflowRuleService import action_policy, originating_run_is_current

            policy = action_policy(db, stage, rule_id, "card.archive")
            if policy is None:
                return {"status": "skipped", "reason": "policy_changed"}
            if not originating_run_is_current(db, policy[0], stage, card):
                return {"status": "skipped", "reason": "workflow_context_changed"}
            delay = policy[1].config["archive_after_seconds"]
            if type(delay) is not int or not 0 <= delay <= 31_536_000:
                return {"status": "skipped", "reason": "policy_changed"}
            entered = card.workflow_stage_entered_at
            if entered is None or card.workflow_stage_entered_key != stage.key:
                return {"status": "skipped", "reason": "entry_evidence_missing"}
            entered = (
                entered.replace(tzinfo=timezone.utc) if entered.tzinfo is None else entered.astimezone(timezone.utc)
            )
            if now < entered + timedelta(seconds=delay):
                return {"status": "skipped", "reason": "not_due"}
            verification = db.exec(
                SqlBuilder.select.table(CardVerificationRecord)
                .where(
                    CardVerificationRecord.card_id == card.id,
                )
                .order_by(CardVerificationRecord.id.desc())
                .limit(1)
                .with_for_update()
            ).first()
            if (
                verification is None
                or verification.decision != "verified"
                or not verification.evidence
                or verification.source_change_seq != card.last_change_seq
            ):
                return {"status": "skipped", "reason": "verification_required"}
            total, completed, started, paused = self.repo.checkitem.get_work_state_counts([card.id]).get(
                card.id, (0, 0, 0, 0)
            )
            if total != completed or started or paused:
                return {"status": "skipped", "reason": "unfinished_work"}
            if pending_card_approvals([card.id]).get(int(card.id), 0):
                return {"status": "skipped", "reason": "approval_pending"}
            if card_dependency_state(db, project.id, card.id)["state"] != "clear":
                return {"status": "skipped", "reason": "dependency_pending"}
            archive = self.repo.project_column.get_or_create_archive_if_not_exists(project.id)
            if not self._change_order(profile, project, card, 0, archive):
                raise ValueError("Automatic archive movement failed")
            effect_run = WorkflowEffectRun(
                stage_id=stage.id,
                stage_key=stage.key,
                stage_revision=stage.edit_revision(),
                project_id=project.id,
                card_id=card.id,
                actor_id=profile.id,
                actor_type="profile",
                profile_id=profile.id,
                profile_name=profile.display_name,
                source="continuous",
                status="succeeded",
                effects=["auto_archive"],
                started_at=started_at,
                finished_at=SafeDateTime.now(),
                duration_ms=max(0, (monotonic_ns() - started_clock) // 1_000_000),
                result={"archived": 1},
            )
            db.insert(effect_run)
            return {"status": "completed", "legacy_effect_run_uid": effect_run.get_uid()}

    @guard_card_app_mutation
    def archive(self, user_or_bot: TUserOrBot, project: TProjectParam | None, card: TCardParam | None) -> bool | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params

        if card.is_linked_resource:
            if not self.can_delete(user_or_bot, card):
                raise CardDeleteForbidden("Only the original card author or an administrator can delete this card")
            return self._delete_card(user_or_bot, project, card)
        if card.archived_at:
            return True

        column_archive = self.repo.project_column.get_or_create_archive_if_not_exists(project.id)

        self.change_order(user_or_bot, project, card, 0, column_archive)

        return True

    @guard_card_app_mutation
    def delete(self, user_or_bot: TUserOrBot, project: TProjectParam | None, card: TCardParam | None) -> bool:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return False
        project, card = params

        if not card.archived_at and not card.is_linked_resource:
            return False

        if not self.can_delete(user_or_bot, card):
            raise CardDeleteForbidden("Only the original card author or an administrator can delete this card")

        return self._delete_card(user_or_bot, project, card)

    def _delete_card(self, user_or_bot: TUserOrBot, project: Project, card: Card) -> bool:
        """Delete only the board-side card and its dependent work records."""

        dependency_children = self._dependency_children(card)
        started_checkitems = self.repo.checkitem.get_all_started_checkitem_by_card(card)

        checkitem_service = self._get_service(CheckitemService)
        current_time = SafeDateTime.now()
        for checkitem in started_checkitems:
            checkitem_service.change_status(
                user_or_bot,
                project,
                card,
                checkitem,
                CheckitemStatus.Stopped,
                current_time,
                should_publish=False,
            )

        with execution_readiness_uow() as execution:
            execution.watch_card_and_dependents(card.id)
            self.repo.card_assigned_user.delete_all_by_card(card)
            self.repo.card_relationship.delete_all_by_card(card)

            BotScopeHelper.delete_by_scope(CardBotScope, card)
            BotScheduleHelper.unschedule_by_scope(CardBotSchedule, card)
            self._get_service(GraphApprovalRequestService).cancel_pending_by_scope(
                project,
                Card.__tablename__,
                card.get_uid(),
                reason="card deleted",
            )

            # Linked cards are disposable references. Purging the board-side row
            # allows the same source to be linked again while its Wiki stays intact.
            is_linked_resource = card.is_linked_resource
            if not is_linked_resource:
                self._get_service(DoclingMetadataService).record_scope_embedding_cleanup(project, card)
            self.repo.card.delete(card, purge=is_linked_resource)
            if not is_linked_resource:
                self._invalidate_source_card_mirrors(card)
            self.repo.card.reoder_after_delete(card.project_column_id, card.order)

        CardPublisher.deleted(project, card)
        self.publish_work_states(project, dependency_children)
        if not is_linked_resource:
            CardActivityTask.card_deleted(user_or_bot, project, card)
            CardBotTask.card_deleted(user_or_bot, project, card)

        return True

    @staticmethod
    def can_delete(user_or_bot: TUserOrBot, card: Card) -> bool:
        """Allow administrators or the immutable creator.

        Legacy cards without any recorded creator are deletable by whoever already
        passed the CardDelete role gate; cards with a known creator stay protected."""

        if card.created_by_user_id is None and card.created_by_bot_id is None:
            return True

        if isinstance(user_or_bot, User):
            return user_or_bot.is_admin or (
                card.created_by_user_id is not None and card.created_by_user_id == user_or_bot.id
            )
        return card.created_by_bot_id is not None and card.created_by_bot_id == user_or_bot.id
