"""Keep card authors useful for workflow while protecting private identity fields."""

import os
from types import SimpleNamespace
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")

from ...domain import CommentPage, SectionPage  # noqa: E402
from ..ports import CardBundleSource, CommentPageSource  # noqa: E402
from . import get_card_bundle  # noqa: E402


def test_card_core_exposes_only_the_public_creator_identity() -> None:
    source = CardBundleSource(
        details={
            "uid": "card",
            "title": "Test",
            "last_change_seq": 42,
            "last_change_target_type": "comment",
            "last_change_at": "2026-09-29T14:00:00Z",
            "last_change_target_uid": "private-target",
            "creator": {
                "uid": "author",
                "type": "user",
                "firstname": "Ada",
                "lastname": "Lovelace",
                "username": "ada",
                "email": "private@example.invalid",
                "is_admin": True,
            },
        },
        checklists=[],
        attachments=[],
        metadata={},
        bot_scopes=[],
        bot_schedules=[],
    )

    result = get_card_bundle(
        SimpleNamespace(get_card_bundle_source=lambda *_: source),
        "project",
        "card",
        CommentPage(),
        SectionPage(),
    )

    assert result.model_dump()["card"]["core"]["creator"] == {
        "uid": "author",
        "type": "user",
        "firstname": "Ada",
        "lastname": "Lovelace",
        "username": "ada",
    }
    core = result.model_dump()["card"]["core"]
    assert core["last_change_seq"] == 42
    assert core["last_change_target_type"] == "comment"
    assert core["last_change_at"] == "2026-09-29T14:00:00Z"
    assert "last_change_target_uid" not in core


@pytest.mark.parametrize("visibility", ["INTERNAL", "SHARED", "PRIVATE"])
@pytest.mark.parametrize("profile", [None, "execute", "review", "triage", "full"])
def test_card_visibility_survives_each_context_profile_without_private_policy(visibility, profile):
    source = CardBundleSource(
        details={"uid": "card", "title": "Authorized card", "visibility": visibility,
                 "owner_user_id": 987, "private_policy": {"secret": "not public"}},
        checklists=[], attachments=[], metadata={}, bot_scopes=[], bot_schedules=[],
    )
    result = get_card_bundle(
        SimpleNamespace(get_card_bundle_source=lambda *_: source,
                        get_comment_page=lambda *_: CommentPageSource([], 0, None)), "project", "card",
        CommentPage(), SectionPage(), profile=profile,
    )
    core = result.model_dump()["card"]["core"]
    assert core["visibility"] == visibility
    assert "owner_user_id" not in core and "private_policy" not in core
