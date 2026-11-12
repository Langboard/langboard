"""Native file descriptors retain the company's current card audience boundary."""

from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, CardAttachment, ProjectAssignedUser, ProjectRole, User
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService


pytestmark = pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)


@pytest.fixture
def attachment_read(current_card):
    user, project, card, card_service = current_card
    CardAttachment.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    descriptor = FileModel(
        storage_type="local",
        storage_name="card_attachment",
        original_filename="clip.mp4",
        filename="immutable.mp4",
        path="/file/opaque/card_attachment/immutable.mp4",
    )
    with DbSession.use(readonly=False) as db:
        role = ProjectRole(project_id=project.id, user_id=user.id, actions=["read"])
        db.insert(role)
        attachment = CardAttachment(user_id=user.id, card_id=card.id, filename="clip.mp4", file=descriptor)
        db.insert(attachment)
    service = CardAttachmentService(lambda _: card_service, lambda _: None, SimpleNamespace())
    return user, project, card, attachment, role, service


def read_file(bundle, *, channel=CollaborationChannel.HumanUI):
    user, project, card, attachment, _, service = bundle
    return service.get_authorized_file(user, project.get_uid(), card.get_uid(), attachment.get_uid(), channel=channel)


@pytest.mark.parametrize("channel", list(CollaborationChannel))
def test_private_owner_is_not_promoted_from_api_or_bot_to_human(attachment_read, channel):
    result = read_file(attachment_read, channel=channel)
    if channel in (CollaborationChannel.HumanUI, CollaborationChannel.Mcp):
        assert result == attachment_read[3].file
    else:
        assert result is None


def test_default_channel_and_client_string_do_not_assert_human_ui(attachment_read):
    user, project, card, attachment, _, service = attachment_read
    assert service.get_authorized_file(user, project.get_uid(), card.get_uid(), attachment.get_uid()) is None
    assert read_file(attachment_read, channel="human_ui") is None


@pytest.mark.parametrize(
    "denial", ["foreign_owner", "external_internal", "deleted_card", "mirror", "foreign_attachment"]
)
def test_unreadable_or_foreign_card_never_returns_descriptor(attachment_read, denial):
    user, project, card, attachment, _, _ = attachment_read
    with DbSession.use(readonly=False) as db:
        if denial == "foreign_owner":
            foreign = User(
                firstname="Foreign",
                lastname="Owner",
                email="foreign@example.invalid",
                password=user.password,
                activated_at=SafeDateTime.now(),
            )
            db.insert(foreign)
            card.owner_user_id = foreign.id
            card.created_by_user_id = foreign.id
        elif denial == "external_internal":
            card.visibility = "INTERNAL"
            card.owner_user_id = None
        elif denial == "deleted_card":
            card.deleted_at = SafeDateTime.now()
        elif denial == "mirror":
            card.source_type = Card.LINKED_RESOURCE_PROJECT_CARD
            card.source_uid = "source"
        else:
            other = Card(
                project_id=project.id,
                title="Other card",
                visibility="PRIVATE",
                owner_user_id=user.id,
                created_by_user_id=user.id,
            )
            db.insert(other)
            attachment.card_id = other.id
            db.update(attachment)
        db.update(card)
    assert read_file(attachment_read) is None


@pytest.mark.parametrize("revocation", ["read_role", "inactive", "deleted_user", "deleted_attachment"])
def test_repeated_lookup_revalidates_current_authority(attachment_read, revocation):
    user, _, _, attachment, role, _ = attachment_read
    assert read_file(attachment_read) == attachment.file
    with DbSession.use(readonly=False) as db:
        if revocation == "read_role":
            db.delete(role)
        elif revocation == "inactive":
            user.activated_at = None
            db.update(user)
        elif revocation == "deleted_user":
            user.deleted_at = SafeDateTime.now()
            db.update(user)
        else:
            attachment.deleted_at = SafeDateTime.now()
            db.update(attachment)
    if revocation == "deleted_attachment":
        assert read_file(attachment_read) is None
    else:
        with pytest.raises(PermissionError):
            read_file(attachment_read)


def test_historical_reference_returns_the_live_replacement_descriptor(attachment_read):
    user, _, card, old, _, _ = attachment_read
    replacement = old.file.model_copy(
        update={"filename": "replacement.mp4", "path": "/file/opaque/card_attachment/replacement.mp4"}
    )
    with DbSession.use(readonly=False) as db:
        current = CardAttachment(user_id=user.id, card_id=card.id, filename="replacement.mp4", file=replacement)
        db.insert(current)
        old.deleted_at = SafeDateTime.now()
        old.replaced_by_id = current.id
        db.update(old)
    assert read_file(attachment_read) == replacement


def test_cached_membership_does_not_survive_removal(attachment_read):
    user, project, _, attachment, _, _ = attachment_read
    with DbSession.use(readonly=False) as db:
        foreign = User(
            firstname="Foreign",
            lastname="Owner",
            email="foreign@example.invalid",
            password=user.password,
            activated_at=SafeDateTime.now(),
        )
        db.insert(foreign)
        project.owner_id = foreign.id
        db.update(project)
        assignment = ProjectAssignedUser(project_id=project.id, user_id=user.id)
        db.insert(assignment)
    assert read_file(attachment_read) == attachment.file
    with DbSession.use(readonly=False) as db:
        db.delete(assignment)
    assert read_file(attachment_read) is None


def test_cached_administrator_flag_is_revalidated(attachment_read):
    user, project, card, attachment, role, service = attachment_read
    with DbSession.use(readonly=False) as db:
        user.is_admin = True
        db.update(user)
        db.delete(role)
    cached = user.model_copy(deep=True)
    assert (
        service.get_authorized_file(
            cached, project.get_uid(), card.get_uid(), attachment.get_uid(), channel=CollaborationChannel.HumanUI
        )
        == attachment.file
    )
    with DbSession.use(readonly=False) as db:
        user.is_admin = False
        db.update(user)
    with pytest.raises(PermissionError):
        service.get_authorized_file(
            cached, project.get_uid(), card.get_uid(), attachment.get_uid(), channel=CollaborationChannel.HumanUI
        )
