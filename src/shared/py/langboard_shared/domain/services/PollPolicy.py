"""Pure named single-choice tally rules. Participation is never approval authority."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PollRules:
    participation_quorum: int
    participation_policy: str
    winner_policy: str
    tie_policy: str
    no_quorum_policy: str
    no_winner_policy: str
    needs_info_policy: str
    allow_change: bool
    tally_visibility: str


@dataclass(frozen=True)
class PollBallot:
    actor_uid: str
    choice: str
    option_uid: str | None


@dataclass(frozen=True)
class PollEvaluation:
    state: str
    phase: str
    reason: str
    winner_option_uid: str | None
    participation_count: int
    vote_count: int
    abstain_count: int
    needs_info_count: int
    counts_by_option: tuple[tuple[str, int], ...]


def validate_poll(rules: PollRules, participant_uids: tuple[str, ...], option_uids: tuple[str, ...]) -> None:
    if not 1 <= len(participant_uids) <= 256 or len(set(participant_uids)) != len(participant_uids):
        raise ValueError("Poll requires distinct bounded participants")
    if not 2 <= len(option_uids) <= 20 or len(set(option_uids)) != len(option_uids):
        raise ValueError("Poll requires 2..20 distinct options")
    if type(rules.participation_quorum) is not int or not 1 <= rules.participation_quorum <= len(participant_uids):
        raise ValueError("Participation quorum must be within the frozen participant count")
    choices = {
        "participation_policy": ("vote_only", "all_responses"),
        "winner_policy": ("plurality", "majority_of_votes"),
        "tie_policy": ("needs_info", "rejected"),
        "no_quorum_policy": ("expired", "rejected"),
        "no_winner_policy": ("needs_info", "rejected"),
        "needs_info_policy": ("record", "block_result"),
        "tally_visibility": ("before", "after_vote", "after_close"),
    }
    if (
        any(
            type(getattr(rules, name)) is not str or getattr(rules, name) not in allowed
            for name, allowed in choices.items()
        )
        or type(rules.allow_change) is not bool
    ):
        raise ValueError("Unsupported Poll rule")


def evaluate_poll(
    rules: PollRules,
    participant_uids: tuple[str, ...],
    option_uids: tuple[str, ...],
    ballots: tuple[PollBallot, ...],
    *,
    now: datetime,
    deadline: datetime,
) -> PollEvaluation:
    """Count latest currently authorized ballots supplied by native persistence.

    Closure is a deadline projection, not a scheduled write or execution permit.
    Winner selection is separate from participation quorum. Abstention/needs_info
    never count as option votes, including under all_responses participation.
    """
    validate_poll(rules, participant_uids, option_uids)
    if now.tzinfo is None or deadline.tzinfo is None:
        raise ValueError("Timezone-aware Poll clock and deadline required")
    actors = set()
    counts = dict.fromkeys(option_uids, 0)
    abstain = needs_info = 0
    for ballot in ballots:
        if ballot.actor_uid not in participant_uids or ballot.actor_uid in actors:
            raise ValueError("Unresolved, duplicate or foreign latest ballot")
        actors.add(ballot.actor_uid)
        if ballot.choice == "vote" and ballot.option_uid in counts:
            counts[ballot.option_uid] += 1
        elif ballot.choice in ("abstain", "needs_info") and ballot.option_uid is None:
            abstain += ballot.choice == "abstain"
            needs_info += ballot.choice == "needs_info"
        else:
            raise ValueError("Invalid single-choice ballot")
    votes = sum(counts.values())
    participation = len(ballots) if rules.participation_policy == "all_responses" else votes
    phase = "closed" if now >= deadline else "open"
    winner = None
    if phase == "open":
        blocked = rules.needs_info_policy == "block_result" and needs_info > 0
        state, reason = ("needs_info", "needs_info_response") if blocked else ("pending", "poll_open")
    elif participation < rules.participation_quorum:
        state, reason = rules.no_quorum_policy, "no_quorum"
    elif rules.needs_info_policy == "block_result" and needs_info:
        state, reason = "needs_info", "needs_info_response"
    elif not votes:
        state, reason = rules.no_winner_policy, "no_valid_votes"
    else:
        highest = max(counts.values())
        leaders = [uid for uid, count in counts.items() if count == highest]
        if len(leaders) > 1:
            state, reason = rules.tie_policy, "tie"
        elif rules.winner_policy == "majority_of_votes" and highest * 2 <= votes:
            state, reason = rules.no_winner_policy, "no_majority"
        else:
            state, reason, winner = "passed", "winner", leaders[0]
    return PollEvaluation(
        state, phase, reason, winner, participation, votes, abstain, needs_info, tuple(counts.items())
    )
