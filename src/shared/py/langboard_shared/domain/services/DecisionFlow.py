"""Native persisted proposal judgments and named single-choice Polls. No execution.

Each case is one immutable round. New policies/targets require a new case; existing
responses cannot be carried forward. All commands revalidate current native card
visibility and explicit read roles before inspecting UUID replay records. Case
creation additionally requires card_update; the frozen participant snapshot authorizes
proposal responses or named Poll ballots, without card editing, delegation, or execution authority.
Poll closure is computed from the deadline on each coherent read; no scheduler or
external effect is invoked. Only currently authorized latest revisions contribute.
The global case revision is a concurrency token and may reveal response activity;
hidden tallies and named choices remain server-filtered, including response receipts.
Coarse native transaction locks also fence noncooperating ACL and card writers.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import exists, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ...helpers import InfraHelper
from ..models import Card, DecisionCase, DecisionOption, DecisionResponse, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction
from .CardVisibilityPolicy import CardVisibility, card_visibility_scope
from .DecisionPolicy import (
    MAX_POLICY_DEPTH,
    MAX_POLICY_NODES,
    DecisionCondition,
    DecisionParticipant,
    DecisionRound,
    InvalidDecisionPolicy,
    ParticipantCondition,
    evaluate_decision,
)
from .DecisionPolicy import (
    DecisionResponse as PolicyResponse,
)
from .PollPolicy import PollBallot, PollRules, evaluate_poll, validate_poll


class DecisionMissing(Exception):
    pass


class DecisionDenied(Exception):
    pass


class DecisionConflict(Exception):
    pass


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _uid(value: str):
    if type(value) is not str or not re.fullmatch(r"[0-9A-Za-z]{11}", value):
        raise ValueError("Invalid UID")
    return InfraHelper.convert_id(value)


def _uuid(value: str) -> str:
    if type(value) is not str:
        raise ValueError("UUID required")
    return str(UUID(value))


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def card_target_revision(card) -> str:
    """Bind the actual native row; explicit content fields defend timestamp-only writers."""
    return _hash(
        {
            "card_uid": card.get_uid(),
            "updated_at": _aware(card.updated_at).isoformat(),
            "title": card.title,
            "description": card.description.model_dump(mode="json"),
            "visibility": card.visibility,
            "owner": str(card.owner_user_id),
            "column": str(card.project_column_id),
            "archived": str(card.archived_at),
        }
    )


def _parse_policy(raw):
    """Bound traversal before recursion; never trust nested JSON shape or operator."""
    stack = [(raw, 1)]
    seen = set()
    while stack:
        node, depth = stack.pop()
        if type(node) is not dict or id(node) in seen or depth > MAX_POLICY_DEPTH:
            raise InvalidDecisionPolicy("Invalid, repeated, cyclic or excessively deep policy")
        seen.add(id(node))
        if len(seen) > MAX_POLICY_NODES:
            raise InvalidDecisionPolicy("Policy node limit exceeded")
        if set(node) == {"actor_uid"}:
            _uid(node["actor_uid"])
            continue
        if set(node) != {"op", "children", "required"} or type(node["children"]) is not list:
            raise InvalidDecisionPolicy("Only concrete actor or explicit operator conditions supported")
        if not 1 <= len(node["children"]) <= MAX_POLICY_NODES:
            raise InvalidDecisionPolicy("Invalid child count")
        stack.extend((child, depth + 1) for child in node["children"])

    def build(node):
        if "actor_uid" in node:
            return ParticipantCondition(node["actor_uid"])
        return DecisionCondition(node["op"], tuple(build(child) for child in node["children"]), node["required"])

    return build(raw)


# One order for policy attachment, claims, and current-authority validation.
_NATIVE_AUTHORITY_TABLES = (
    'organization, "user", setting_role, scim_group, scim_group_member, '
    "project, project_role, project_assigned_user, board_team, board_team_member, "
    "card, employee_membership_policy, user_identity_link, "
    "decision_case, decision_option, decision_response"
)


class DecisionFlow:
    def __init__(self, service):
        self.service = service

    @contextmanager
    def _transaction(self, *, read_only=False, actor_sources=False):
        if DbSession.has_active_transaction():
            raise DecisionConflict("Decision commands require their own transaction boundary")
        dialect = DbEngine.get_main_engine().dialect.name
        try:
            with DbSession.atomic(sensitive=True) as db:
                if read_only:
                    if dialect == "postgresql":
                        db.exec(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                    elif dialect == "sqlite":
                        db.exec(text("BEGIN"))
                    else:
                        raise RuntimeError("Decision persistence supports SQLite and PostgreSQL")
                elif dialect == "sqlite":
                    db.exec(text("BEGIN IMMEDIATE"))
                elif dialect == "postgresql":
                    db.exec(text("SET LOCAL lock_timeout = '3s'"))
                    db.exec(text("SET LOCAL statement_timeout = '10s'"))
                    # Source creation uses the canonical Group/Team writer order.
                    # Table locks cover absent memberships and noncooperating native
                    # ACL writers; row locks alone cannot fence an absent source row.
                    db.exec(text("LOCK TABLE " + _NATIVE_AUTHORITY_TABLES + " IN SHARE ROW EXCLUSIVE MODE"))
                else:
                    raise RuntimeError("Decision persistence supports SQLite and PostgreSQL")
                yield db
        except IntegrityError as exc:
            raise DecisionConflict("Concurrent or duplicate response") from exc
        except OperationalError as exc:
            code = getattr(exc.orig, "sqlstate", None)
            if code in ("55P03", "57014", "40P01", "40001") or (
                dialect == "sqlite" and "locked" in str(exc.orig).lower()
            ):
                raise DecisionConflict("Retry with the same request UUID") from exc
            raise

    def _authorized(self, db, actor, project_uid, card_uid, channel, *, manage=False):
        if not isinstance(actor, User):
            raise DecisionDenied()
        _uid(project_uid)
        _uid(card_uid)
        resolved = self.service.card.resolve_readable_card(project_uid, card_uid, actor, channel)
        if resolved is None:
            raise DecisionMissing()
        project, card, _ = resolved
        if card.is_linked_resource:
            raise DecisionDenied("Create decisions on the canonical card")
        roles = db.exec(
            SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id
            )
        ).all()
        required = [ProjectRoleAction.Read]
        if manage:
            required.append(ProjectRoleAction.CardUpdate)
        if not any(role.is_granted(required) for role in roles):
            raise DecisionDenied()
        return project, card

    def _authorization(self, db, participant_uids, project_id, card, channel):
        """Batch roles/users and resolve each actor once within this transaction only."""
        if not participant_uids:
            return frozenset()
        ids = [_uid(uid) for uid in participant_uids]
        users = db.exec(SqlBuilder.select.table(User).where(User.id.in_(ids))).all()
        roles = db.exec(
            SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project_id,
                ProjectRole.user_id.in_(ids),
            )
        ).all()
        readers = {int(role.user_id) for role in roles if role.is_granted(ProjectRoleAction.Read)}
        authorized = set()
        for user in users:
            if int(user.id) not in readers:
                continue
            resolved = self.service.card.resolve_visibility_context(project_id, user, channel)
            if resolved and resolved[1].can_read_card(
                CardVisibility(card.visibility), owner_user_id=card.owner_user_id
            ):
                authorized.add(user.get_uid())
        return frozenset(authorized)

    def _eligible(self, db, case, card, channel, authorization=None):
        expected = {"proposal": "proposal_response", "poll": "poll_vote", "graph_approval": "graph_approval_response"}.get(case.source_kind)
        if expected is None or case.snapshot.get("authorization") != expected:
            raise DecisionDenied("Unsupported participant authorization")
        participants = frozenset(case.snapshot["participant_uids"])
        if authorization is None:
            authorization = self._authorization(db, participants, case.project_id, card, channel)
        eligible = participants & authorization
        if case.actor_snapshot is not None:
            from .DecisionActors import DecisionActors

            eligible &= DecisionActors.retained_actor_uids(db, case.actor_snapshot)
        return eligible

    @staticmethod
    def _validate_policy_snapshot(case):
        if case.source_kind not in ("proposal", "graph_approval"):
            return
        # A stored policy edit must never reinterpret existing responses while
        # retaining the old round token, including on the UUID replay path.
        if case.policy_revision != _hash(
            {"policy": case.snapshot["policy"], "participant_uids": case.snapshot["participant_uids"]}
        ):
            raise DecisionConflict("Decision policy snapshot changed; create a new round")

    @staticmethod
    def _round(case):
        DecisionFlow._validate_policy_snapshot(case)
        return DecisionRound(
            case.get_uid(),
            case.round_id,
            case.target_revision,
            case.policy_revision,
            tuple(DecisionParticipant(uid) for uid in case.snapshot["participant_uids"]),
            _parse_policy(case.snapshot["policy"]),
            _aware(case.deadline),
        )

    def _evaluation(self, db, case, card, channel, now, authorization=None, *, dispatch=False):
        responses = db.exec(
            SqlBuilder.select.table(DecisionResponse)
            .where(DecisionResponse.decision_id == case.id)
            .order_by(DecisionResponse.response_order)
        ).all()
        inputs = tuple(
            PolicyResponse(
                case.get_uid(),
                row.round_id,
                case.target_revision,
                case.policy_revision,
                InfraHelper.convert_uid(row.actor_id),
                row.choice,
                _aware(row.responded_at),
                row.response_order,
            )
            for row in responses
        )
        evaluation_time = (
            max((_aware(row.responded_at) for row in responses), default=now)
            if not dispatch and case.state in ("passed", "rejected")
            else now
        )
        return evaluate_decision(
            self._round(case),
            inputs,
            now=evaluation_time,
            authorized_actor_ids=self._eligible(db, case, card, channel, authorization),
        )

    def _with_actor_snapshot(self, db, summary, case, card, actor, channel, source_access):
        if case.actor_snapshot is None:
            return summary  # Existing concrete-user summaries keep their exact shape.
        from .DecisionActors import DecisionActors

        access = source_access if source_access is not None else {}
        grouped = any(source["kind"] == "group" for source in case.actor_snapshot["sources"])
        if grouped not in access:
            try:
                _, _, _, abilities = DecisionActors(self.service)._authorized(
                    db, actor, InfraHelper.convert_uid(case.project_id), card.get_uid(), channel
                )
                access[grouped] = not grouped or abilities["select_group"]
            except (DecisionDenied, DecisionMissing):
                access[grouped] = False
        return {**summary, "actor_snapshot": case.actor_snapshot if access[grouped] else None}

    def _summary(self, db, case, card, actor, channel, now, authorization=None, source_access=None):
        if case.source_kind == "poll":
            return self._with_actor_snapshot(
                db,
                self._poll_summary(db, case, card, actor, channel, now, authorization),
                case,
                card,
                actor,
                channel,
                source_access,
            )
        evaluation = self._evaluation(db, case, card, channel, now, authorization)
        target_current = card_target_revision(card) == case.target_revision
        authority_current = case.state not in ("passed", "rejected") or evaluation.state == case.state
        state = evaluation.state if target_current and authority_current else "cancelled"
        summary = {
            "uid": case.get_uid(),
            "round_id": case.round_id,
            "source_kind": case.source_kind,
            "source_ref": card.get_uid(),
            "question": case.question,
            "revision": case.revision,
            "target_revision": case.target_revision,
            "policy_revision": case.policy_revision,
            "participant_uids": case.snapshot["participant_uids"],
            "participant_authorization": case.snapshot["authorization"],
            "policy": case.snapshot["policy"],
            "state": state,
            "deadline": _aware(case.deadline).isoformat() if case.deadline else None,
            "active_responders": list(evaluation.active_responders) if target_current and authority_current else [],
            "allowed_actions": ["respond"]
            if target_current
            and authority_current
            and actor.get_uid() in evaluation.active_responders
            and state in ("pending", "needs_info")
            else [],
            "blocked_reason": "target_revision_changed"
            if not target_current
            else "terminal_authority_changed"
            if not authority_current
            else None,
            "effect_result": None,
        }
        if case.source_kind == "graph_approval":
            summary["approval_uid"] = case.snapshot["native_binding"]["approval_uid"]
            summary["automatic_resume"] = False
        return self._with_actor_snapshot(db, summary, case, card, actor, channel, source_access)

    def _poll_data(self, db, case, card, channel, now, authorization=None):
        options = db.exec(
            SqlBuilder.select.table(DecisionOption)
            .where(DecisionOption.decision_id == case.id)
            .order_by(DecisionOption.position)
        ).all()
        # Correlated latest-revision query returns at most one row per frozen actor,
        # regardless of the number of immutable changes retained in history.
        later = DecisionResponse.__table__.alias("later_response")
        rows = db.exec(
            SqlBuilder.select.table(DecisionResponse)
            .where(
                DecisionResponse.decision_id == case.id,
                DecisionResponse.round_id == case.round_id,
                ~exists().where(
                    later.c.decision_id == DecisionResponse.decision_id,
                    later.c.round_id == DecisionResponse.round_id,
                    later.c.actor_id == DecisionResponse.actor_id,
                    later.c.response_revision > DecisionResponse.response_revision,
                ),
            )
            .order_by(DecisionResponse.response_order)
        ).all()
        eligible = self._eligible(db, case, card, channel, authorization)
        rows = [row for row in rows if InfraHelper.convert_uid(row.actor_id) in eligible]
        ballots = tuple(
            PollBallot(
                InfraHelper.convert_uid(row.actor_id),
                row.choice,
                InfraHelper.convert_uid(row.option_id) if row.option_id else None,
            )
            for row in rows
        )
        evaluation = evaluate_poll(
            PollRules(**case.snapshot["poll"]),
            tuple(case.snapshot["participant_uids"]),
            tuple(option.get_uid() for option in options),
            ballots,
            now=now,
            deadline=_aware(case.deadline),
        )
        return options, rows, eligible, evaluation

    def _poll_summary(self, db, case, card, actor, channel, now, authorization=None):
        options, rows, eligible, result = self._poll_data(db, case, card, channel, now, authorization)

        def ballot(row):
            return {
                "actor_uid": InfraHelper.convert_uid(row.actor_id),
                "choice": row.choice,
                "option_uid": InfraHelper.convert_uid(row.option_id) if row.option_id else None,
                "response_revision": row.response_revision,
                "response_order": row.response_order,
                "responded_at": _aware(row.responded_at).isoformat(),
            }

        mine = next((ballot(row) for row in rows if row.actor_id == actor.id), None)
        settings = case.snapshot["poll"]
        visible = (
            settings["tally_visibility"] == "before"
            or settings["tally_visibility"] == "after_vote"
            and mine is not None
            or settings["tally_visibility"] == "after_close"
            and result.phase == "closed"
        )
        target_current = card_target_revision(card) == case.target_revision
        can_respond = (
            target_current
            and result.phase == "open"
            and actor.get_uid() in eligible
            and (mine is None or settings["allow_change"])
        )
        active = [actor.get_uid()] if can_respond else []
        return {
            "uid": case.get_uid(),
            "round_id": case.round_id,
            "source_kind": "poll",
            "source_ref": card.get_uid(),
            "question": case.question,
            "revision": case.revision,
            "target_revision": case.target_revision,
            "policy_revision": case.policy_revision,
            "participant_uids": case.snapshot["participant_uids"],
            "participant_authorization": case.snapshot["authorization"],
            "policy": None,
            "state": result.state if target_current else "cancelled",
            "deadline": _aware(case.deadline).isoformat(),
            "active_responders": active,
            "allowed_actions": ["respond"] if can_respond else [],
            "blocked_reason": None if target_current else "target_revision_changed",
            "effect_result": None,
            "poll": {
                "options": [{"uid": option.get_uid(), "key": option.key, "label": option.label} for option in options],
                "settings": settings,
                "phase": result.phase,
                "my_response": mine,
                "can_change": can_respond and mine is not None,
                "tally_visible": visible,
                "tally": {
                    "participation_count": result.participation_count,
                    "vote_count": result.vote_count,
                    "abstain_count": result.abstain_count,
                    "needs_info_count": result.needs_info_count,
                    "counts_by_option": dict(result.counts_by_option),
                }
                if visible
                else None,
                "responses": [ballot(row) for row in rows] if visible else None,
                "winner_option_uid": result.winner_option_uid if visible and target_current else None,
                "outcome_reason": (result.reason if visible else "poll_" + result.phase)
                if target_current
                else "target_revision_changed",
            },
        }

    def create(self, actor, project_uid, card_uid, payload, *, channel=CollaborationChannel.Api):
        # Public service entry accepts validated forms, not ad hoc coercion.
        data = payload.model_dump(mode="json")
        request_id = _uuid(data["request_id"])
        sources = data.get("actor_sources")
        if sources is None:
            # New optional defaults must not alter already persisted UUID hashes.
            data.pop("actor_sources", None)
            data.pop("expected_actor_snapshot", None)
        if data.get("approval_uid") is None:
            data.pop("approval_uid", None)
        is_poll = data["source_kind"] == "poll"
        if not is_poll:
            data.pop("poll", None)  # Preserve the original persisted proposal request hash.
        root = None if is_poll else _parse_policy(data["policy"])
        participant_uids = data["participant_uids"]
        if any(type(uid) is not str for uid in participant_uids):
            raise ValueError("Concrete user UIDs required")
        for uid in participant_uids:
            _uid(uid)
        if (
            data["source_kind"] not in ("proposal", "poll", "graph_approval")
            or data["source_ref"] != card_uid
            or not data["question"].strip()
        ):
            raise ValueError("Only native proposal or Poll judgments bound to this card are supported")
        with self._transaction(actor_sources=sources is not None) as db:
            project, card = self._authorized(db, actor, project_uid, card_uid, channel, manage=True)
            resolver = None
            if sources is not None:
                from .DecisionActors import DecisionActors

                resolver = DecisionActors(self.service)
                resolver.authorize_sources_in_transaction(
                    db, actor, project_uid, card_uid, sources, source_kind="proposal" if data["source_kind"] == "graph_approval" else data["source_kind"], channel=channel
                )
            request_hash = _hash({"project_uid": project_uid, "card_uid": card_uid, **data})
            # No replay disclosure until current caller authority has passed.
            previous = db.exec(
                SqlBuilder.select.table(DecisionCase).where(
                    DecisionCase.creator_id == actor.id, DecisionCase.request_id == request_id
                )
            ).first()
            now = datetime.now(timezone.utc)
            if previous:
                if previous.request_hash != request_hash:
                    raise DecisionConflict("Request UUID payload mismatch")
                return {**self._summary(db, previous, card, actor, channel, now), "replayed": True}
            if card_target_revision(card) != data["target_revision"]:
                raise DecisionConflict("Target revision changed")
            approval = None
            if data["source_kind"] == "graph_approval":
                from .DecisionHITL import DecisionHITL

                approval = DecisionHITL.bindable(db, project, card, data["approval_uid"])
            actor_snapshot = None
            if resolver is not None:
                actor_snapshot = resolver.resolve_in_transaction(
                    db, actor, project_uid, card_uid, sources, source_kind="proposal" if data["source_kind"] == "graph_approval" else data["source_kind"], channel=channel
                )
                if (
                    actor_snapshot["snapshot"] != data["expected_actor_snapshot"]
                    or participant_uids != actor_snapshot["actor_uids"]
                ):
                    raise DecisionConflict("Actor preview changed; refresh and reconfirm the selection")
            deadline = payload.deadline
            if deadline is not None and deadline <= now:
                raise ValueError("Deadline must be in the future")
            policy_revision = _hash({"policy": data["policy"], "participant_uids": participant_uids})
            if is_poll:
                rules = {key: value for key, value in data["poll"].items() if key != "options"}
                validate_poll(
                    PollRules(**rules),
                    tuple(participant_uids),
                    tuple(option["key"] for option in data["poll"]["options"]),
                )
                policy_revision = _hash({"poll": data["poll"], "participant_uids": participant_uids})
            frozen = DecisionRound(
                "new",
                str(uuid4()),
                data["target_revision"],
                policy_revision,
                tuple(DecisionParticipant(uid) for uid in participant_uids),
                root,
                deadline,
            )
            if not is_poll:
                evaluate_decision(frozen, (), now=now, authorized_actor_ids=frozenset(participant_uids))
            case = DecisionCase(
                project_id=project.id,
                card_id=card.id,
                creator_id=actor.id,
                request_id=request_id,
                request_hash=request_hash,
                round_id=frozen.round_id,
                source_kind=data["source_kind"],
                question=data["question"],
                target_revision=frozen.target_revision,
                policy_revision=policy_revision,
                snapshot={
                    "participant_uids": participant_uids,
                    "policy": data["policy"],
                    "authorization": "poll_vote" if is_poll else "graph_approval_response" if approval else "proposal_response",
                    **({"native_binding": {"approval_uid": approval.get_uid(), "execution_plan": approval.execution_plan}} if approval else {}),
                    **({"poll": rules} if is_poll else {}),
                },
                deadline=SafeDateTime.fromtimestamp(deadline.timestamp(), timezone.utc) if deadline else None,
                actor_snapshot=actor_snapshot,
            )
            authorization = self._authorization(db, participant_uids, case.project_id, card, channel)
            if self._eligible(db, case, card, channel, authorization) != frozenset(participant_uids):
                raise DecisionDenied("Participants require current card read authority")
            db.insert(case)
            if approval is not None:
                approval.decision_case_id = case.id
                db.update(approval)
            if is_poll:
                for position, option in enumerate(data["poll"]["options"]):
                    db.insert(DecisionOption(decision_id=case.id, position=position, **option))
            return {**self._summary(db, case, card, actor, channel, now, authorization), "replayed": False}

    def list(self, actor, project_uid, card_uid, *, channel=CollaborationChannel.Api, limit=50, before=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        # Primary coherent read snapshot: GET never takes the mutation table locks.
        with self._transaction(read_only=True) as db:
            project, card = self._authorized(db, actor, project_uid, card_uid, channel)
            query = SqlBuilder.select.table(DecisionCase).where(
                DecisionCase.project_id == project.id, DecisionCase.card_id == card.id
            )
            if before is not None:
                query = query.where(DecisionCase.id < _uid(before))
            cases = db.exec(query.order_by(DecisionCase.id.desc()).limit(limit + 1)).all()
            selected = []
            participant_uids = set()
            for case in cases[:limit]:
                combined = participant_uids | set(case.snapshot["participant_uids"])
                if selected and len(combined) > MAX_POLICY_NODES:
                    break
                selected.append(case)
                participant_uids = combined
            # A page contains at most 256 distinct actors even across 50 cases.
            # Current native identity/visibility is resolved once per actor and reused
            # only inside this coherent read transaction, never across requests.
            authorization = self._authorization(db, participant_uids, project.id, card, channel)
            now = datetime.now(timezone.utc)
            source_access = {}
            return {
                "items": [
                    self._summary(db, case, card, actor, channel, now, authorization, source_access)
                    for case in selected
                ],
                "next_cursor": selected[-1].get_uid() if len(cases) > len(selected) else None,
                "target_revision": card_target_revision(card),
            }

    def my_decisions(self, actor, project_uid, *, channel=CollaborationChannel.Api, limit=50, before=None):
        """Outstanding active responses only; bound each page to 50 readable cases.

        A sparse page can be empty with a continuation. Clients must follow the
        cursor until it is null, rather than interpreting an empty page as EOF.
        """
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        project_id = _uid(project_uid)
        cursor = _uid(before) if before is not None else None
        with self._transaction(read_only=True) as db:
            resolved = self.service.card.resolve_visibility_context(project_id, actor, channel)
            if resolved is None or not resolved[1].project_member:
                raise DecisionMissing()
            roles = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project_id, ProjectRole.user_id == actor.id
                )
            ).all()
            if not any(role.is_granted(ProjectRoleAction.Read) for role in roles):
                raise DecisionDenied()
            query = (
                select(DecisionCase, Card)
                .join(Card, (Card.id == DecisionCase.card_id) & (Card.project_id == DecisionCase.project_id))
                .where(
                    DecisionCase.project_id == project_id,
                    Card.deleted_at.is_(None),
                    card_visibility_scope(resolved[1]),
                )
            )
            if cursor is not None:
                query = query.where(DecisionCase.id < cursor)
            candidates = db.exec(query.order_by(DecisionCase.id.desc()).limit(51)).all()
            items = []
            scanned = 0
            participant_checks = 0
            now = datetime.now(timezone.utc)
            for case, card in candidates[:50]:
                selected = not card.is_linked_resource and actor.get_uid() in case.snapshot["participant_uids"]
                # Keep current identity resolution within the same bound as a
                # card listing, even when many large policies share this inbox.
                if selected and participant_checks + len(case.snapshot["participant_uids"]) > MAX_POLICY_NODES:
                    break
                scanned += 1
                if not selected:
                    continue
                participant_checks += len(case.snapshot["participant_uids"])
                summary = self._summary(db, case, card, actor, channel, now)
                # Poll response changes are available from the card, but a ballot
                # already submitted is no longer an outstanding inbox task.
                if "respond" not in summary["allowed_actions"]:
                    continue
                if case.source_kind == "poll" and summary["poll"]["my_response"] is not None:
                    continue
                items.append(summary)
                if len(items) == limit:
                    break
            return {
                "items": items,
                "next_cursor": candidates[scanned - 1][0].get_uid() if len(candidates) > scanned else None,
            }

    def respond(self, actor, project_uid, decision_uid, payload, *, channel=CollaborationChannel.Api):
        data = payload.model_dump(mode="json")
        request_id = _uuid(data["request_id"])
        # Immutable source provenance selects the lock set only. All case/authority
        # facts are loaded again under the command fence before any disclosure.
        with DbEngine.get_main_engine().connect() as connection:
            captured = connection.execute(
                select(DecisionCase.actor_snapshot).where(
                    DecisionCase.id == _uid(decision_uid), DecisionCase.project_id == _uid(project_uid)
                )
            ).scalar_one_or_none()
        with self._transaction(actor_sources=captured is not None) as db:
            case = db.exec(
                SqlBuilder.select.table(DecisionCase).where(
                    DecisionCase.id == _uid(decision_uid), DecisionCase.project_id == _uid(project_uid)
                )
            ).first()
            if case is None:
                raise DecisionMissing()
            _, card = self._authorized(db, actor, project_uid, InfraHelper.convert_uid(case.card_id), channel)
            self._validate_policy_snapshot(case)
            if actor.get_uid() not in case.snapshot["participant_uids"]:
                raise DecisionDenied("Not a frozen participant")
            if case.actor_snapshot is not None and actor.get_uid() not in self._eligible(db, case, card, channel):
                raise DecisionDenied("Captured source membership or current authority revoked")
            if case.source_kind in ("proposal", "graph_approval"):
                if data["choice"] == "vote" or data.get("option_uid") is not None:
                    raise ValueError("Proposal responses cannot select Poll options")
                data.pop("option_uid", None)  # Preserve legacy response request hashes.
            request_hash = _hash({"project_uid": project_uid, "decision_uid": decision_uid, **data})
            previous = db.exec(
                SqlBuilder.select.table(DecisionResponse).where(
                    DecisionResponse.actor_id == actor.id, DecisionResponse.request_id == request_id
                )
            ).first()
            if previous:
                if previous.request_hash != request_hash:
                    raise DecisionConflict("Request UUID payload mismatch")
                receipt = self._with_actor_snapshot(db, previous.receipt, case, card, actor, channel, None)
                return {**receipt, "replayed": True}
            if (
                data["round_id"] != case.round_id
                or data["expected_revision"] != case.revision
                or data["target_revision"] != case.target_revision
                or data["policy_revision"] != case.policy_revision
                or card_target_revision(card) != case.target_revision
            ):
                raise DecisionConflict("Round, policy, target or expected revision changed")
            now = datetime.now(timezone.utc)
            authorization = self._authorization(db, case.snapshot["participant_uids"], case.project_id, card, channel)
            if case.source_kind == "poll":
                return self._respond_poll(
                    db, case, card, actor, channel, now, authorization, data, request_id, request_hash
                )
            current = self._evaluation(db, case, card, channel, now, authorization)
            if (
                case.state in ("passed", "rejected", "cancelled", "expired")
                or current.state not in ("pending", "needs_info")
                or actor.get_uid() not in current.active_responders
            ):
                raise DecisionConflict("Decision closed, actor responded, or sequence stage inactive")
            order = case.response_order + 1
            response = DecisionResponse(
                decision_id=case.id,
                round_id=case.round_id,
                actor_id=actor.id,
                request_id=request_id,
                request_hash=request_hash,
                choice=data["choice"],
                response_order=order,
                responded_at=SafeDateTime.fromtimestamp(now.timestamp(), timezone.utc),
                receipt={},
            )
            db.insert(response)
            case.response_order = order
            case.revision += 1
            case.state = self._evaluation(db, case, card, channel, now, authorization).state
            db.update(case)
            receipt = {
                **self._summary(db, case, card, actor, channel, now, authorization),
                "response_order": order,
                "choice": response.choice,
                "replayed": False,
            }
            response.receipt = receipt
            db.update(response)
            return receipt

    def _respond_poll(self, db, case, card, actor, channel, now, authorization, data, request_id, request_hash):
        options, rows, eligible, result = self._poll_data(db, case, card, channel, now, authorization)
        if actor.get_uid() not in eligible:
            raise DecisionDenied("Participant no longer authorized")
        if result.phase != "open":
            raise DecisionConflict("Poll deadline reached")
        if data["choice"] not in ("vote", "abstain", "needs_info"):
            raise ValueError("Unsupported Poll response")
        option = next((item for item in options if item.get_uid() == data.get("option_uid")), None)
        if data["choice"] == "vote" and option is None:
            raise ValueError("Option must belong to this Poll")
        previous = next((row for row in rows if row.actor_id == actor.id), None)
        if previous and not case.snapshot["poll"]["allow_change"]:
            raise DecisionConflict("Poll response changes disabled")
        response = DecisionResponse(
            decision_id=case.id,
            round_id=case.round_id,
            actor_id=actor.id,
            request_id=request_id,
            request_hash=request_hash,
            choice=data["choice"],
            option_id=option.id if option else None,
            response_revision=previous.response_revision + 1 if previous else 1,
            response_order=case.response_order + 1,
            responded_at=SafeDateTime.fromtimestamp(now.timestamp(), timezone.utc),
            receipt={},
        )
        db.insert(response)
        case.response_order += 1
        case.revision += 1
        case.state = self._poll_data(db, case, card, channel, now, authorization)[3].state
        db.update(case)
        receipt = {
            **self._summary(db, case, card, actor, channel, now, authorization),
            "response_order": response.response_order,
            "response_revision": response.response_revision,
            "choice": response.choice,
            "option_uid": data.get("option_uid"),
            "replayed": False,
        }
        response.receipt = receipt
        db.update(response)
        return receipt
