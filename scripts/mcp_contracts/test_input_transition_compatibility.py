"""Check legacy sample arguments and rollback boundaries against captured schemas."""

import json
import sys
from pathlib import Path
from jsonschema import Draft202012Validator


def main(baseline_path: Path, candidate_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text())
    candidate = json.loads(candidate_path.read_text())
    old = {tool["name"]: tool["input_schema"] for tool in baseline["contract"]["tools"]}
    new = {tool["name"]: tool["input_schema"] for tool in candidate["contract"]["tools"]}
    cases = {
        "change_card_details": {"project_uid": "p", "card_uid": "c"},
        "create_card": {
            "project_uid": "p",
            "column_uid": "column",
            "title": "title",
            "description": None,
            "assign_user_uids": None,
        },
        "create_card_in_leftmost_column": {"project_uid": "p", "title": "title"},
        "search_project_cards": {"project_uid": "p", "query": "query"},
        "update_bot_hook": {
            "project_uid": "p",
            "bot_uid": "b",
            "target_table": "card",
            "hook_uid": "h",
            "events": None,
            "active": None,
        },
        "upsert_bot_hook": {
            "project_uid": "p",
            "bot_uid": "b",
            "target_table": "card",
            "target_uid": "t",
            "events": [],
            "active": True,
        },
    }
    for name, payload in cases.items():
        Draft202012Validator(old[name]).validate(payload)
        Draft202012Validator(new[name]).validate(payload)
    for name in ("update_bot_hook", "upsert_bot_hook"):
        old_events = old[name]["$defs"]["BotTriggerCondition"]["enum"]
        for event in old_events:
            payload = {**cases[name], "events": [event]}
            Draft202012Validator(old[name]).validate(payload)
            Draft202012Validator(new[name]).validate(payload)
    for name, field, value in (("create_card", "next_action", "draft"), ("search_project_cards", "limit", 10)):
        payload = {**cases[name], field: value}
        assert Draft202012Validator(new[name]).is_valid(payload)
        assert not Draft202012Validator(old[name]).is_valid(payload)
    print("Legacy request examples accepted; new-argument rollback rejection confirmed.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_input_transition_compatibility.py BASELINE CANDIDATE")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
