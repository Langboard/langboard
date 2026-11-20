"""Persisted image comments retain immutable provenance through compaction reads."""

import importlib
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard.mcp_tools import CardImagesMcp
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.domain.models import Card, CardAttachment, CardComment, ProjectRole
from langboard_shared.domain.models.AttachmentStorageObject import AttachmentStorageObject, AttachmentStorageReference
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardCommentCompactionService import CommentCompactionError
from PIL import Image
from sqlalchemy import update
from test_comment_compaction_command import compact_command, draft  # noqa: F401


@pytest.fixture
def image_compaction(compact_command, monkeypatch):  # noqa: F811
    compaction, actor, project, card, binding, *_ = compact_command
    native = DomainService()
    engine = DbEngine.get_main_engine()
    for model in (CardAttachment, AttachmentStorageObject, AttachmentStorageReference):
        model.__table__.create(engine)
    token = Encryptor.encrypt("local", "storage_type")
    with DbSession.atomic() as db:
        attachment = CardAttachment(
            card_id=card.id,
            user_id=actor.id,
            filename="image.png",
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="original.png",
                original_filename="image.png",
                path=f"/file/{token}/card_attachment/original.png",
            ),
        )
        db.insert(attachment)
    output = BytesIO()
    Image.new("RGB", (40, 30)).save(output, format="PNG")
    payload = output.getvalue()
    # Native comment creation reads FileModel locators through the real storage
    # resolver. Stub only provider bytes on that path, preserving provenance.
    monkeypatch.setattr(
        Storage._storages["local"], "download", lambda _name, _file, destination: bool(destination.write(payload))
    )
    downloads = Mock(wraps=Storage.download_file)
    monkeypatch.setattr(Storage, "download_file", downloads)
    monkeypatch.setattr(
        CardImagesMcp,
        "Env",
        SimpleNamespace(PUBLIC_UI_URL="https://board.example", API_URL="https://board.example/api"),
    )
    monkeypatch.setattr(native.card, "mark_card_changed", Mock())
    anchor = dict(
        type="ImageRegionSelector",
        version=1,
        source=dict(type="card_attachment", attachment_uid=attachment.get_uid(), revision=attachment.image_revision),
        shape="rectangle",
        image_width=40,
        image_height=30,
        x=12.5,
        y=20,
        width=10,
        height=5,
    )
    receipt = native.card_comment.create(
        actor,
        project,
        card,
        EditorContentModel(content="Review exact pixels"),
        anchor,
        request_uid=str(uuid4()),
        dispatch_effects=False,
    )
    assert receipt and receipt["replayed"] is False
    snapshot, _ = compaction._snapshot(project, card)
    ref = "comment:" + receipt["comment_uid"]
    assert (
        json.loads(next(source.text for source in snapshot.sources if source.evidence_ref == ref))["anchor"] == anchor
    )
    repository = compaction.repo.card_comment_compaction
    record = repository.claim(repository.reserve(card, actor, binding, snapshot))
    assert repository.complete(record, record.attempt, snapshot, binding, draft(snapshot))
    downloads.reset_mock()

    # Evidence is a read: fail immediately if it starts storage writes or indexing.
    def forbidden(*args, **kwargs):
        pytest.fail("Evidence reads must not upload or start embedding work")

    monkeypatch.setattr(Storage, "upload", forbidden)
    monkeypatch.setattr(Storage, "upload_named", forbidden)
    index = importlib.import_module("langboard_shared.tasks.docling.CompactionMetadataIndexTask")
    monkeypatch.setattr(index, "enqueue_index", forbidden)
    monkeypatch.setattr(index, "create_document_embeddings", forbidden)
    return SimpleNamespace(
        service=compaction,
        native=native,
        actor=actor,
        project=project,
        card=card,
        attachment=attachment,
        anchor=anchor,
        ref=ref,
        record=record,
        downloads=downloads,
        revision=snapshot.source_revision(),
    )


def evidence(b):
    page = b.service.get_evidence(b.actor, b.project, b.card, b.record.id, evidence_ref=b.ref)
    assert page["source_revision"] == b.revision
    assert page["compaction_uid"] == b.record.get_uid()
    assert len(page["records"]) == 1
    original = page["records"][0]
    assert original["evidence_ref"] == b.ref and original["kind"] == "comment"
    assert original["data"]["anchor"] == b.anchor
    b.downloads.assert_not_called()
    return page


@pytest.mark.parametrize("mutation", ["unchanged", "replaced", "deleted", "private", "role_revoked"])
def test_image_evidence_survives_compaction_with_current_source_and_acl(image_compaction, mutation):
    b = image_compaction
    view = b.service.get_view(b.actor, b.project, b.card)
    assert [entry["kind"] for entry in view["records"]] == ["compaction"]
    evidence(b)
    with DbSession.use(readonly=False) as db:
        original_comment = db.exec(
            SqlBuilder.select.table(CardComment).where(CardComment.image_request_uid.is_not(None))
        ).all()[0]
        original_values = original_comment.model_dump(mode="json")
        original_attachment = db.exec(SqlBuilder.select.table(CardAttachment)).all()[0].model_dump(mode="json")
    with DbSession.atomic() as db:
        if mutation == "replaced":
            file = b.attachment.file.model_copy(
                update={
                    "filename": "replacement.png",
                    "path": b.attachment.file.path.replace("original.png", "replacement.png"),
                }
            )
            db.exec(update(CardAttachment).where(CardAttachment.id == b.attachment.id).values(file=file))
        elif mutation == "deleted":
            row = db.exec(SqlBuilder.select.table(CardAttachment)).all()[0]
            db.delete(row)
        elif mutation == "private":
            db.exec(
                update(Card)
                .where(Card.id == b.card.id)
                .values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2, created_by_bot_id=None)
            )
        elif mutation == "role_revoked":
            db.exec(update(ProjectRole).where(ProjectRole.user_id == b.actor.id).values(actions=[]))
    if mutation in {"private", "role_revoked"}:
        with pytest.raises(CommentCompactionError) as denied:
            evidence(b)
        assert denied.value.status in {403, 404}
        if mutation == "private":
            with pytest.raises(ValueError):
                CardImagesMcp.read_card_images(
                    b.project.get_uid(),
                    b.card.get_uid(),
                    b.actor,
                    b.native,
                    include_attachment_uids=[b.anchor["source"]["attachment_uid"]],
                    include_body_images=False,
                )
        b.downloads.assert_not_called()
    else:
        evidence(b)
        result = CardImagesMcp.read_card_images(
            b.project.get_uid(),
            b.card.get_uid(),
            b.actor,
            b.native,
            include_attachment_uids=[b.anchor["source"]["attachment_uid"]],
            include_body_images=False,
        )
        included = result.structured_content["included"]
        if mutation == "deleted":
            assert included == [] and result.structured_content["omitted"]
            b.downloads.assert_not_called()
        elif mutation == "replaced":
            # A current-attachment read exposes its changed revision; the old
            # evidence selector retains the original immutable revision.
            assert len(included) == 1
            assert included[0]["image_revision"] != b.anchor["source"]["revision"]
            assert b.downloads.call_args.args[0].filename == "replacement.png"
        else:
            assert len(included) == 1
            assert included[0]["image_revision"] == b.anchor["source"]["revision"]
            assert b.downloads.call_args.args[0].filename == "original.png"
    # Neither compaction nor evidence reads rewrite original comments or remove
    # attachment rows. Only the explicit fixture mutation may change attachment.
    with DbSession.use(readonly=False) as db:
        saved = db.exec(
            SqlBuilder.select.table(CardComment, with_deleted=True).where(CardComment.id == original_comment.id)
        ).all()[0]
        assert saved.model_dump(mode="json") == original_values
        attached = db.exec(
            SqlBuilder.select.table(CardAttachment, with_deleted=True).where(CardAttachment.id == b.attachment.id)
        ).all()[0]
        if mutation in {"unchanged", "private", "role_revoked"}:
            assert attached.model_dump(mode="json") == original_attachment
        elif mutation == "deleted":
            assert attached.deleted_at is not None
        else:
            assert attached.deleted_at is None and attached.image_revision != b.anchor["source"]["revision"]
