"""Next action uses the current card audience and original CardUpdate role."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_tools import CardMcp
from langboard.routes.board import BoardCardApi
from langboard.routes.board.forms.Card import ChangeCardDetailsForm
from langboard_shared.core.db import DbSession
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"next_action": "Review"}, {"next_action": "Review"}),
        ({"next_action": None}, {"next_action": None}),
        ({"next_action": ""}, {"next_action": None}),
        ({"title": "Renamed"}, {"title": "Renamed"}),
    ],
)
def test_current_private_owner_update_preserves_omission_and_clear(current_card, payload, expected):
    user, project, card, reader = current_card
    update = Mock(return_value=expected)
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=reader.resolve_readable_card, update=update))
    response = BoardCardApi.change_card_details(
        project.get_uid(),
        card.get_uid(),
        SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.HumanUI}),
        ChangeCardDetailsForm(**payload),
        user,
        service,
    )
    update.assert_called_once_with(user, project.get_uid(), card.get_uid(), expected)
    assert response.status_code == 200


def test_next_action_does_not_grant_api_audience_or_foreign_owner(current_card):
    user, project, card, reader = current_card
    update = Mock()
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=reader.resolve_readable_card, update=update))
    for channel in [CollaborationChannel.Api, CollaborationChannel.Bot]:
        with pytest.raises(ApiException.NotFound_404):
            BoardCardApi.change_card_details(
                project.get_uid(),
                card.get_uid(),
                SimpleNamespace(scope={"collaboration_channel": channel}),
                ChangeCardDetailsForm(next_action="Secret"),
                user,
                service,
            )
    with DbSession.use(readonly=False) as db:
        card.owner_user_id = user.id + 1
        card.created_by_user_id = card.owner_user_id
        db.update(card)
    with pytest.raises(ApiException.NotFound_404):
        BoardCardApi.change_card_details(
            project.get_uid(),
            card.get_uid(),
            SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.HumanUI}),
            ChangeCardDetailsForm(next_action="Secret"),
            user,
            service,
        )
    update.assert_not_called()


def test_mcp_next_action_requires_original_update_role_and_current_audience(current_card, monkeypatch):
    user, project, card, reader = current_card
    assert McpRoleFilter.get_filtered(CardMcp.change_card_details)[1] == [ProjectRoleAction.CardUpdate.value]
    update = Mock(return_value={"next_action": "Review"})
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=reader.resolve_readable_card, update=update))
    assert CardMcp.change_card_details(project.get_uid(), card.get_uid(), user, service, next_action="Review") == {
        "next_action": "Review"
    }
    with DbSession.use(readonly=False) as db:
        card.owner_user_id = user.id + 1
        card.created_by_user_id = card.owner_user_id
        db.update(card)
    with pytest.raises(Exception, match="CARD_UNAVAILABLE"):
        CardMcp.change_card_details(project.get_uid(), card.get_uid(), user, service, next_action="Hidden")
    assert update.call_count == 1
