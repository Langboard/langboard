"""Native card action owners reject complete invalid sets before any mutation."""

from unittest.mock import Mock
import pytest
from langboard.mcp_tools.CardActionsMcp import CardChange, update_card
from langboard.mcp_tools.RoleChecker import McpRoleChecker
from pydantic import TypeAdapter
from src.api.tests.card_workspace.test_card_change_counts import counts  # noqa: F401
from src.api.tests.mcp_integration.test_followup_mutation_boundary import (
    native_followup,  # noqa: F401
    persisted_counts,
)


@pytest.mark.parametrize(
    "people,labels,error",
    [
        (["missing"], None, "Unknown project member"),
        (None, ["missing"], "Unknown label"),
        (["member", "member"], None, "duplicates"),
        (None, ["label", "label"], "duplicates"),
    ],
)
def test_native_owner_rejects_entire_sets_without_writes(native_followup, people, labels, error):  # noqa: F811
    n = native_followup
    with pytest.raises(ValueError, match=error):
        n.f.cards.replace_people_and_labels(n.f.user, n.f.project, n.f.card, people, labels)
    assert persisted_counts(n.f) == [0, 0, 0]
    assert not any(effect.called for effect in n.effects)


def test_native_owner_does_not_apply_valid_member_subset_when_label_is_unknown(native_followup):  # noqa: F811
    n = native_followup
    with pytest.raises(ValueError, match="Unknown label"):
        n.f.cards.replace_people_and_labels(n.f.user, n.f.project, n.f.card, [n.f.user.get_uid()], ["missing"])
    assert persisted_counts(n.f) == [0, 0, 0]
    assert not any(effect.called for effect in n.effects)


@pytest.mark.parametrize("action,field", [("assign", "assign_user_uids"), ("label", "label_uids")])
def test_typed_action_uses_native_complete_set_validation(native_followup, monkeypatch, action, field):  # noqa: F811
    n = native_followup
    # Isolate complete-set business validation; transport grants have separate tests.
    monkeypatch.setattr(McpRoleChecker, "check_permission", lambda *args: True)
    owner = Mock(wraps=n.f.cards.replace_people_and_labels)
    monkeypatch.setattr(n.f.cards, "replace_people_and_labels", owner)
    change = TypeAdapter(CardChange).validate_python({"action": action, field: ["missing"]})
    with pytest.raises(ValueError, match="Unknown"):
        update_card(n.f.project.get_uid(), n.f.card.get_uid(), change, n.f.user, n.service)
    owner.assert_called_once()
    assert persisted_counts(n.f) == [0, 0, 0]
    assert not any(effect.called for effect in n.effects)
