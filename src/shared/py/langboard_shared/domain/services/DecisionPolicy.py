"""Pure approval policy contract. Callers supply server-derived authorization.

This module neither grants permissions nor executes effects. Poll options and delegated
approval are unsupported: adapters must resolve an explicitly authorized approval policy.
Persistence must fence these snapshots and current ACL atomically when accepting a vote.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


DecisionState = Literal["pending", "needs_info", "passed", "rejected", "expired", "cancelled"]
PolicyOperator = Literal["all", "any", "k_of_n", "sequence"]
ResponseChoice = Literal["approve", "reject", "abstain", "needs_info"]
MAX_POLICY_DEPTH = 32
MAX_POLICY_NODES = 256
MAX_RESPONSES = 4096


class InvalidDecisionPolicy(ValueError):
    """Untrusted or ambiguous policy input; no decision may be inferred."""


@dataclass(frozen=True)
class DecisionParticipant:
    """A concrete actor resolved from the frozen participant configuration."""

    actor_id: str


@dataclass(frozen=True)
class ParticipantCondition:
    actor_id: str


@dataclass(frozen=True)
class DecisionCondition:
    op: PolicyOperator
    children: tuple["DecisionCondition | ParticipantCondition", ...]
    required: int = 0


@dataclass(frozen=True)
class DecisionRound:
    decision_id: str
    round_id: str
    target_revision: str
    policy_revision: str
    participants: tuple[DecisionParticipant, ...]
    policy: DecisionCondition | ParticipantCondition
    deadline: datetime | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class DecisionResponse:
    decision_id: str
    round_id: str
    target_revision: str
    policy_revision: str
    actor_id: str
    choice: ResponseChoice
    responded_at: datetime
    response_order: int


@dataclass(frozen=True)
class IgnoredDecisionResponse:
    index: int
    reason: str


@dataclass(frozen=True)
class DecisionEvaluation:
    state: DecisionState
    active_responders: tuple[str, ...] = ()
    ignored_responses: tuple[IgnoredDecisionResponse, ...] = ()


@dataclass(frozen=True)
class _Result:
    state: DecisionState
    active: tuple[str, ...] = ()
    passed_order: int | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidDecisionPolicy(message)


def _identifier(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 512 and bool(value.strip())


def _timestamp(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _validate(
    snapshot: DecisionRound,
    responses: tuple[DecisionResponse, ...],
    now: datetime,
    authorized_actor_ids: frozenset[str],
) -> None:
    _require(type(snapshot) is DecisionRound, "Expected an immutable DecisionRound")
    _require(_timestamp(now), "now must be timezone-aware")
    _require(
        type(authorized_actor_ids) is frozenset and len(authorized_actor_ids) <= MAX_POLICY_NODES,
        "Current authorization must be a bounded frozenset",
    )
    _require(all(_identifier(actor) for actor in authorized_actor_ids), "Invalid authorized actor")
    for name in ("decision_id", "round_id", "target_revision", "policy_revision"):
        _require(_identifier(getattr(snapshot, name)), f"Invalid {name}")
    _require(type(snapshot.cancelled) is bool, "cancelled must be boolean")
    _require(snapshot.deadline is None or _timestamp(snapshot.deadline), "Deadline must be timezone-aware")
    _require(type(snapshot.participants) is tuple, "Participants must be an immutable tuple")
    _require(1 <= len(snapshot.participants) <= MAX_POLICY_NODES, "Invalid participant count")
    actors: set[str] = set()
    for participant in snapshot.participants:
        _require(type(participant) is DecisionParticipant, "Unsupported participant or delegated permission")
        _require(_identifier(participant.actor_id), "Invalid participant actor")
        _require(participant.actor_id not in actors, "Duplicate participant")
        actors.add(participant.actor_id)

    # Validate the entire tree before any short circuit, including inactive stages.
    seen: set[int] = set()
    visiting: set[int] = set()
    references: set[str] = set()
    stack = [(snapshot.policy, 1, False)]
    while stack:
        node, depth, leaving = stack.pop()
        if leaving:
            visiting.remove(id(node))
            continue
        _require(depth <= MAX_POLICY_DEPTH, "Policy depth limit exceeded")
        _require(id(node) not in visiting, "Policy cycle")
        _require(id(node) not in seen, "Duplicate node reference")
        seen.add(id(node))
        _require(len(seen) <= MAX_POLICY_NODES, "Policy node limit exceeded")
        if type(node) is ParticipantCondition:
            _require(_identifier(node.actor_id) and node.actor_id in actors, "Unknown participant reference")
            _require(node.actor_id not in references, "Duplicate actor reference")
            references.add(node.actor_id)
            continue
        _require(type(node) is DecisionCondition, "Unsupported condition")
        _require(type(node.op) is str and node.op in ("all", "any", "k_of_n", "sequence"), "Unsupported operator")
        _require(
            type(node.children) is tuple and 1 <= len(node.children) <= MAX_POLICY_NODES,
            "Conditions require bounded immutable nonempty children",
        )
        _require(type(node.required) is int, "Quorum must be an integer")
        if node.op == "k_of_n":
            _require(1 <= node.required <= len(node.children), "Invalid quorum")
        else:
            _require(node.required == 0, "Quorum only applies to k_of_n")
        visiting.add(id(node))
        stack.append((node, depth, True))
        stack.extend((child, depth + 1, False) for child in reversed(node.children))
    _require(references == actors, "Every participant must have exactly one policy reference")

    _require(type(responses) is tuple and len(responses) <= MAX_RESPONSES, "Responses must be a bounded tuple")
    response_keys: set[tuple[str, ...]] = set()
    order_keys: set[tuple[str, str, int]] = set()
    for response in responses:
        _require(type(response) is DecisionResponse, "Unsupported response")
        for name in ("decision_id", "round_id", "target_revision", "policy_revision", "actor_id"):
            _require(_identifier(getattr(response, name)), f"Invalid response {name}")
        _require(
            type(response.choice) is str and response.choice in ("approve", "reject", "abstain", "needs_info"),
            "Unsupported response choice; read, reaction, poll and delegation are not approval",
        )
        _require(_timestamp(response.responded_at), "Response time must be timezone-aware")
        _require(type(response.response_order) is int and response.response_order >= 1, "Invalid response order")
        order_key = (response.decision_id, response.round_id, response.response_order)
        _require(order_key not in order_keys, "Ambiguous response order")
        order_keys.add(order_key)
        key = (
            response.decision_id,
            response.round_id,
            response.target_revision,
            response.policy_revision,
            response.actor_id,
        )
        _require(key not in response_keys, "Duplicate response; resolve history/idempotency before evaluation")
        response_keys.add(key)


def evaluate_decision(
    snapshot: DecisionRound,
    responses: tuple[DecisionResponse, ...],
    *,
    now: datetime,
    authorized_actor_ids: frozenset[str],
) -> DecisionEvaluation:
    """Evaluate a frozen round with current authorization and an explicit clock.

    A reject makes a threshold fail only when passage becomes impossible. Abstention
    stays pending, never approves or invents a rejection. Needs-info propagates when
    passage remains possible. Cancellation precedes expiry; a still-open round at its
    deadline expires. Persisted terminal outcomes belong to the caller's lifecycle.

    SEQUENCE requires a trusted server-assigned response_order, strictly greater than
    the order completing prior stages. Timestamps alone cannot establish eligibility.
    Premature votes are ignored; actors remain eligible when their stage activates.
    The caller must assign ordinals atomically in the round; request payloads are not
    trusted evidence of ordering. This does not implement submission authorization.
    Current authorization is externally verified input, not an ACL check by this module.
    """
    _validate(snapshot, responses, now, authorized_actor_ids)
    actors = {participant.actor_id for participant in snapshot.participants}
    accepted: dict[str, tuple[int, DecisionResponse]] = {}
    ignored: dict[int, str] = {}
    for index, response in enumerate(responses):
        reason = None
        if response.decision_id != snapshot.decision_id:
            reason = "other_decision"
        elif response.round_id != snapshot.round_id:
            reason = "other_round"
        elif response.target_revision != snapshot.target_revision:
            reason = "target_revision_mismatch"
        elif response.policy_revision != snapshot.policy_revision:
            reason = "policy_revision_mismatch"
        elif response.actor_id not in actors:
            reason = "not_participant"
        elif response.actor_id not in authorized_actor_ids:
            reason = "authorization_revoked"
        elif response.responded_at > now:
            reason = "future_response"
        elif snapshot.deadline is not None and response.responded_at >= snapshot.deadline:
            reason = "response_after_deadline"
        if reason:
            ignored[index] = reason
        else:
            accepted[response.actor_id] = (index, response)

    visited: set[str] = set()

    def evaluate(node: DecisionCondition | ParticipantCondition, since: int = 0) -> _Result:
        if type(node) is ParticipantCondition:
            visited.add(node.actor_id)
            entry = accepted.get(node.actor_id)
            if entry and entry[1].response_order <= since:
                ignored[entry[0]] = "inactive_sequence_stage"
                entry = None
            if entry is None:
                active = (node.actor_id,) if node.actor_id in authorized_actor_ids else ()
                return _Result("pending", active)
            response = entry[1]
            if response.choice == "approve":
                return _Result("passed", passed_order=response.response_order)
            if response.choice == "reject":
                return _Result("rejected")
            return _Result("needs_info" if response.choice == "needs_info" else "pending")
        if node.op == "sequence":
            boundary = since
            for child in node.children:
                result = evaluate(child, boundary)
                if result.state != "passed":
                    return result
                boundary = result.passed_order
            return _Result("passed", passed_order=boundary)
        results = [evaluate(child, since) for child in node.children]
        required = node.required if node.op == "k_of_n" else 1 if node.op == "any" else len(results)
        passed = [result.passed_order for result in results if result.state == "passed"]
        if len(passed) >= required:
            return _Result("passed", passed_order=sorted(passed)[required - 1])
        if sum(result.state != "rejected" for result in results) < required:
            return _Result("rejected")
        state = "needs_info" if any(result.state == "needs_info" for result in results) else "pending"
        return _Result(state, tuple(actor for result in results for actor in result.active))

    if snapshot.cancelled:
        result = _Result("cancelled")
    elif snapshot.deadline is not None and now >= snapshot.deadline:
        result = _Result("expired")
    else:
        result = evaluate(snapshot.policy)
        for actor_id, (index, _) in accepted.items():
            if actor_id not in visited:
                ignored[index] = "inactive_sequence_stage"
    return DecisionEvaluation(
        result.state,
        result.active,
        tuple(IgnoredDecisionResponse(index, reason) for index, reason in sorted(ignored.items())),
    )
