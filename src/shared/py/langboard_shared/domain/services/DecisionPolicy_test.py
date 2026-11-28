"""Native pure policy contract tests; runnable without application initialization."""

import importlib.util
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parametrize(names, rows):
    """Run independent contract cases with standard-library subtests."""

    def decorate(function):
        def run(self):
            for row in rows:
                values = row if "," in names else (row,)
                with self.subTest(case=values):
                    function(self, *values)

        return run

    return decorate


# Application service imports initialize DB/config. Load this stdlib-only kernel directly.
_spec = importlib.util.spec_from_file_location("native_decision_policy", Path(__file__).with_name("DecisionPolicy.py"))
policy = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = policy
_spec.loader.exec_module(policy)
DecisionCondition = policy.DecisionCondition
ParticipantCondition = policy.ParticipantCondition
NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def leaf(actor):
    return ParticipantCondition(actor)


def condition(op, *children, required=0):
    return DecisionCondition(op, tuple(children), required)


def snapshot(root=None, actors=("a",)):
    return policy.DecisionRound(
        "decision",
        "round1",
        "target1",
        "policy1",
        tuple(policy.DecisionParticipant(actor) for actor in actors),
        root or leaf("a"),
    )


def vote(actor="a", choice="approve", order=1, **changes):
    return replace(
        policy.DecisionResponse(
            "decision", "round1", "target1", "policy1", actor, choice, NOW - timedelta(seconds=10), order
        ),
        **changes,
    )


def evaluate(round_snapshot, *responses, authorized=None, **changes):
    return policy.evaluate_decision(
        round_snapshot,
        tuple(responses),
        now=NOW,
        authorized_actor_ids=frozenset(
            authorized if authorized is not None else (p.actor_id for p in round_snapshot.participants)
        ),
        **changes,
    )


class DecisionPolicyContractTests(unittest.TestCase):
    """Execute directly to avoid importing the application service package."""

    def test_single_response_states_and_immutability(self):
        round_snapshot = snapshot()
        assert evaluate(round_snapshot).state == "pending"
        assert evaluate(round_snapshot).active_responders == ("a",)
        for choice, expected in (
            ("approve", "passed"),
            ("reject", "rejected"),
            ("abstain", "pending"),
            ("needs_info", "needs_info"),
        ):
            result = evaluate(round_snapshot, vote(choice=choice))
            assert result.state == expected
            assert result.active_responders == ()
        with self.assertRaises(FrozenInstanceError):
            round_snapshot.round_id = "round2"
        with self.assertRaises(FrozenInstanceError):
            round_snapshot.participants[0].actor_id = "attacker"

    @_parametrize(
        "changes,reason",
        [
            ({"decision_id": "elsewhere"}, "other_decision"),
            ({"round_id": "old"}, "other_round"),
            ({"target_revision": "old"}, "target_revision_mismatch"),
            ({"policy_revision": "old"}, "policy_revision_mismatch"),
            ({"actor_id": "outsider"}, "not_participant"),
            ({"responded_at": NOW + timedelta(seconds=1)}, "future_response"),
        ],
    )
    def test_unrelated_or_stale_votes_never_approve(self, changes, reason):
        result = evaluate(snapshot(), vote(**changes))
        assert result.state == "pending"
        assert result.active_responders == ("a",)
        assert result.ignored_responses[0].reason == reason

    def test_current_revocation_removes_previous_approval_and_response_eligibility(self):
        result = evaluate(snapshot(), vote(), authorized=())
        assert result.state == "pending"
        assert result.active_responders == ()
        assert result.ignored_responses[0].reason == "authorization_revoked"

    def test_all_any_and_threshold_aggregation_do_not_invent_veto_or_approval(self):
        actors = ("a", "b", "c")
        quorum = snapshot(condition("k_of_n", *(leaf(a) for a in actors), required=2), actors)
        assert evaluate(quorum, vote("a")).state == "pending"
        assert evaluate(quorum, vote("a"), vote("b", order=2)).state == "passed"
        assert evaluate(quorum, vote("a", "reject")).state == "pending"
        assert evaluate(quorum, vote("a", "reject"), vote("b", "reject", 2)).state == "rejected"
        assert evaluate(quorum, vote("a"), vote("b", "abstain", 2)).state == "pending"
        assert evaluate(quorum, vote("a"), vote("b", "needs_info", 2)).state == "needs_info"
        assert evaluate(quorum, vote("a"), vote("b", order=2), vote("c", "reject", 3)).state == "passed"
        any_round = snapshot(condition("any", leaf("a"), leaf("b")), ("a", "b"))
        assert evaluate(any_round, vote("a", "reject"), vote("b", order=2)).state == "passed"
        all_round = replace(any_round, policy=condition("all", leaf("a"), leaf("b")))
        assert evaluate(all_round, vote("a", "reject")).state == "rejected"

    def test_nested_two_of_three_and_security_singleton(self):
        root = condition("all", condition("k_of_n", leaf("a"), leaf("b"), leaf("c"), required=2), leaf("security"))
        round_snapshot = snapshot(root, ("a", "b", "c", "security"))
        responses = (vote("a"), vote("b", order=2))
        result = evaluate(round_snapshot, *responses)
        assert result.state == "pending"
        assert result.active_responders == ("security",)
        assert evaluate(round_snapshot, *responses, vote("security", order=3)).state == "passed"
        assert evaluate(round_snapshot, *responses, vote("security", "reject", 3)).state == "rejected"

    def test_sequence_only_activates_next_stage_and_requires_trusted_order(self):
        round_snapshot = snapshot(condition("sequence", leaf("a"), leaf("b")), ("a", "b"))
        result = evaluate(round_snapshot, vote("b"))
        assert result.active_responders == ("a",)
        assert result.ignored_responses[0].reason == "inactive_sequence_stage"
        # All timestamps are equal. Arrival order, rather than timestamps or tuple order, is decisive.
        result = evaluate(round_snapshot, vote("a", order=2), vote("b", order=1))
        assert result.state == "pending"
        assert result.active_responders == ("b",)
        assert result.ignored_responses[0].index == 1
        assert evaluate(round_snapshot, vote("b", order=2), vote("a", order=1)).state == "passed"

    def test_nested_sequence_uses_threshold_completion_order(self):
        root = condition(
            "sequence",
            condition("k_of_n", leaf("a"), leaf("b"), leaf("c"), required=2),
            condition("all", leaf("d"), leaf("e")),
        )
        round_snapshot = snapshot(root, tuple("abcde"))
        responses = (vote("a", order=1), vote("b", order=3), vote("c", order=5), vote("d", order=2), vote("e", order=4))
        result = evaluate(round_snapshot, *responses)
        assert result.state == "pending"
        assert result.active_responders == ("d",)
        assert evaluate(round_snapshot, *responses[:3], vote("d", order=6), vote("e", order=4)).state == "passed"

    def test_deadline_and_cancellation_are_explicit_and_validation_still_runs(self):
        round_snapshot = replace(snapshot(), deadline=NOW)
        assert evaluate(round_snapshot, vote()).state == "expired"
        assert evaluate(replace(round_snapshot, cancelled=True), vote()).state == "cancelled"
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(replace(round_snapshot, cancelled=True, policy=condition("unknown", leaf("a"))))

    @_parametrize(
        "root,actors",
        [
            (condition("all"), ("a",)),
            (condition("k_of_n", leaf("a"), required=0), ("a",)),
            (condition("k_of_n", leaf("a"), required=2), ("a",)),
            (condition("k_of_n", leaf("a"), required=True), ("a",)),
            (condition("all", leaf("a"), required=1), ("a",)),
            (condition("delegated", leaf("a")), ("a",)),
            (condition("any", leaf("a"), leaf("a")), ("a",)),
            (condition("any", leaf("a"), leaf("missing")), ("a",)),
            (leaf("a"), ("a", "a")),
            (leaf("a"), ("a", "b")),
            (condition("any", leaf("a"), True), ("a",)),
            (DecisionCondition("all", [leaf("a")]), ("a",)),
        ],
    )
    def test_invalid_untrusted_policies_fail_before_short_circuit(self, root, actors):
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(snapshot(root, actors), vote())

    def test_cycles_shared_references_depth_and_node_bounds(self):
        cycle = condition("all", leaf("a"))
        object.__setattr__(cycle, "children", (cycle,))
        shared = leaf("a")
        invalid_roots = [cycle, condition("any", shared, shared)]
        deep = leaf("a")
        for _ in range(policy.MAX_POLICY_DEPTH):
            deep = condition("all", deep)
        invalid_roots.append(deep)
        for root in invalid_roots:
            with self.assertRaises(policy.InvalidDecisionPolicy):
                evaluate(snapshot(root), vote())
        actors = tuple(str(i) for i in range(policy.MAX_POLICY_NODES))
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(snapshot(condition("all", *(leaf(a) for a in actors)), actors))
        valid = snapshot(condition("all", *(leaf(a) for a in actors[:-1])), actors[:-1])
        assert evaluate(valid).state == "pending"

    @_parametrize("choice", ["read", "reaction", "delegate", "poll_option", True])
    def test_unsupported_response_types_do_not_become_approvals(self, choice):
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(snapshot(), vote(choice=choice))

    def test_ambiguous_history_order_and_mutable_inputs_rejected(self):
        round_snapshot = snapshot(condition("all", leaf("a"), leaf("b")), ("a", "b"))
        for responses in (
            (vote(), vote(order=2)),
            (vote(), vote("b")),
            (vote(order=0),),
            (vote(responded_at=NOW.replace(tzinfo=None)),),
        ):
            with self.assertRaises(policy.InvalidDecisionPolicy):
                evaluate(round_snapshot, *responses)
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(replace(snapshot(), participants=[policy.DecisionParticipant("a")]))
        with self.assertRaises(policy.InvalidDecisionPolicy):
            policy.evaluate_decision(snapshot(), [], now=NOW, authorized_actor_ids=frozenset({"a"}))
        with self.assertRaises(policy.InvalidDecisionPolicy):
            policy.evaluate_decision(snapshot(), (), now=NOW, authorized_actor_ids={"a"})

    def test_input_limits_and_timezone_validation(self):
        for changes in ({"decision_id": "x" * 513}, {"deadline": NOW.replace(tzinfo=None)}, {"cancelled": 1}):
            with self.assertRaises(policy.InvalidDecisionPolicy):
                evaluate(replace(snapshot(), **changes))
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(snapshot(), *(vote(round_id=str(i)) for i in range(policy.MAX_RESPONSES + 1)))
        with self.assertRaises(policy.InvalidDecisionPolicy):
            evaluate(snapshot(), authorized=tuple(str(i) for i in range(policy.MAX_POLICY_NODES + 1)))
        deep = leaf("a")
        for _ in range(policy.MAX_POLICY_DEPTH - 1):
            deep = condition("all", deep)
        assert evaluate(snapshot(deep), vote()).state == "passed"

    def test_pure_result_contains_no_effect_execution_or_mutated_inputs(self):
        round_snapshot, response = snapshot(), vote()
        before = repr((round_snapshot, response))
        first = evaluate(round_snapshot, response)
        assert first == evaluate(round_snapshot, response)
        assert first.state == "passed"
        assert not hasattr(first, "effect_result")
        assert repr((round_snapshot, response)) == before


if __name__ == "__main__":
    unittest.main()
