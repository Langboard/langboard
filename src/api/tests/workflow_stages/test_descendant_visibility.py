"""Family moves reauthorize descendants through the native visibility policy."""

from types import MethodType
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ProjectAssignedUser, User
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from sqlalchemy import delete
from test_descendant_move import family as family  # noqa: F401
from test_descendant_move import move, positions


def native_visibility(f, monkeypatch):
    # The fixture actor is external; card visibility and current membership use
    # the production primary reads instead of the family fixture's resolver stub.
    with DbSession.atomic() as db:
        for card in [f.card, *f.children]:
            card.visibility = "SHARED"
            db.update(card)
    monkeypatch.setattr(f.service, "_resolve_internal_access", lambda _: False)
    monkeypatch.setattr(f.service, "resolve_readable_card", MethodType(CardService.resolve_readable_card, f.service))


@pytest.mark.parametrize("owner_id", [1, 2])
def test_private_descendant_requires_current_owner(request, monkeypatch, owner_id):
    f = request.getfixturevalue("family")
    native_visibility(f, monkeypatch)
    with DbSession.atomic() as db:
        if owner_id != f.actor.id:
            db.insert(
                User(
                    id=owner_id,
                    firstname="Other",
                    lastname="Owner",
                    email="other@example.invalid",
                    password="test-only",
                    activated_at=SafeDateTime.now(),
                )
            )
        child = f.children[1]
        child.visibility = "PRIVATE"
        child.owner_user_id = owner_id
        child.created_by_user_id = owner_id
        db.update(child)

    prompt = move(f, channel=CollaborationChannel.HumanUI)
    assert set(prompt) == {"confirmation_required", "confirmation_revision"}
    if owner_id == f.actor.id:
        result = move(
            f,
            move_descendants=True,
            confirmation_revision=prompt["confirmation_revision"],
            channel=CollaborationChannel.HumanUI,
        )
        assert result is True
        assert set(positions(f).values()) == {f.new.id}
    else:
        with pytest.raises(ValueError, match="complete card family"):
            move(
                f,
                move_descendants=True,
                confirmation_revision=prompt["confirmation_revision"],
                channel=CollaborationChannel.HumanUI,
            )
        assert set(positions(f).values()) == {f.old.id}
        assert not f.notifications


def test_membership_revocation_after_confirmation_rejects_family(request, monkeypatch):
    f = request.getfixturevalue("family")
    native_visibility(f, monkeypatch)
    with DbSession.atomic() as db:
        other = User(
            id=2,
            firstname="Board",
            lastname="Owner",
            email="board-owner@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(other)
        f.project.owner_id = other.id
        db.update(f.project)
        db.insert(ProjectAssignedUser(project_id=f.project.id, user_id=f.actor.id))
    prompt = move(f, channel=CollaborationChannel.HumanUI)
    with f.engine.begin() as connection:
        connection.execute(
            delete(ProjectAssignedUser).where(
                ProjectAssignedUser.project_id == f.project.id,
                ProjectAssignedUser.user_id == f.actor.id,
            )
        )
    with pytest.raises(ValueError, match="complete card family"):
        move(
            f,
            move_descendants=True,
            confirmation_revision=prompt["confirmation_revision"],
            channel=CollaborationChannel.HumanUI,
        )
    assert set(positions(f).values()) == {f.old.id}
    assert not f.notifications
