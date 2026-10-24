"""Persist image anchors, immutable receipts and fresh native authority on SQLite."""

from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.board.forms.Comment import CreateCardCommentForm
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, CardAttachment, CardComment, ProjectAssignedUser, ProjectRole, User
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.infrastructure.repositories.factory.CardCommentRepository import CardCommentRepository
from PIL import Image
from pydantic import ValidationError
from test_attachment_duplicate_upload import choose, rows, upload
from test_attachment_duplicate_upload import uploads as uploads
from test_card_mirrors import mirror_board as mirror_board


def anchor(attachment):
    return dict(
        type="ImageRegionSelector",
        version=1,
        source=dict(type="card_attachment", attachment_uid=attachment.get_uid(), revision=attachment.image_revision),
        shape="point",
        image_width=1920,
        image_height=1080,
        x=12.5,
        y=20,
        width=0,
        height=0,
    )


@pytest.fixture
def images(mirror_board, monkeypatch):  # noqa: F811 - imported native fixture
    b = mirror_board
    output = BytesIO()
    Image.new("RGB", (1920, 1080)).save(output, format="PNG")
    b.image_bytes = output.getvalue()

    def download(storage, name, filename, destination):
        destination.write(b.image_bytes)
        return True

    b.download = Mock(side_effect=download)
    monkeypatch.setattr(Storage, "_download", b.download)
    CardAttachment.__table__.create(b.engine)
    CardComment.__table__.create(b.engine)
    with DbSession.use(readonly=False) as db:
        b.image = CardAttachment(
            card_id=b.source.id,
            user_id=b.actor.id,
            filename="image.png",
            file=FileModel(
                storage_type="local",
                storage_name="attachments",
                original_filename="image.png",
                filename="immutable-image-key",
                path="/file/one",
            ),
        )
        db.insert(b.image)
    b.service.mark_card_changed = Mock()
    b.comments = CardCommentService(
        lambda _: b.service, lambda _: None, SimpleNamespace(card_comment=CardCommentRepository(None, None))
    )
    b.comments.dispatch_created = Mock()
    return b


def create(b, *, request_uid=None, image_anchor=None, content="Review image", actor=None):
    return b.comments.create(
        actor or b.actor,
        b.source_board,
        b.source,
        EditorContentModel(content=content),
        image_anchor or anchor(b.image),
        request_uid=request_uid or str(uuid4()),
    )


def comments():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(CardComment, with_deleted=True)).all()


@pytest.mark.parametrize(
    "change",
    [
        {"x": True},
        {"x": "1"},
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": -1},
        {"image_width": True},
        {"image_height": 1.5},
        {"image_width": "1920"},
        {"image_width": 100001},
        {"width": 1},
        {"shape": "rectangle"},
        {"x": 1921},
        {"version": True},
        {"version": 2},
        {"extra": "rejected"},
        {"source": {"type": "card_attachment", "attachment_uid": "x", "revision": "ABC"}},
    ],
)
def test_malformed_selectors_rejected_at_form_and_service(images, change):
    value = {**anchor(images.image), **change}
    with pytest.raises(ValidationError):
        CreateCardCommentForm(content="Image", anchor=value)
    with pytest.raises(ValidationError):
        create(images, image_anchor=value)
    assert comments() == []


def test_union_preserves_legacy_text_and_valid_rectangle(images):
    assert CreateCardCommentForm(content="text", anchor={"exact": "quote"}).anchor.type == "TextQuoteSelector"
    value = {**anchor(images.image), "shape": "rectangle", "width": 20, "height": 30}
    assert create(images, image_anchor=value)["replayed"] is False
    assert comments()[0].anchor == value
    # Exercise the existing text service path with a native repository.
    text = images.comments.create(
        images.actor, images.source_board, images.source, EditorContentModel(content="Text"), {"exact": "quote"}
    )
    assert isinstance(text, CardComment) and text.anchor["type"] == "TextQuoteSelector"


def test_revision_uses_immutable_identity_and_survives_rename_and_hash_backfill(images):
    image = images.image
    revision = image.image_revision
    image.filename = "renamed.png"
    image.file.path = "/signed/new-url"
    image.file.original_filename = "renamed.png"
    image.content_sha256 = "a" * 64
    assert image.image_revision == revision and image.api_response()["image_revision"] == revision
    assert "storage_type" not in image.api_response()
    image.file.filename = "changed-byte-key"
    assert image.image_revision != revision


def test_retry_is_durable_after_content_edit_and_deletion(images):
    b = images
    uid = str(uuid4())
    first = create(b, request_uid=uid)
    assert first["replayed"] is False
    with DbSession.use(readonly=False) as db:
        row = comments()[0]
        row.content = EditorContentModel(content="Edited")
        db.update(row)
    assert create(b, request_uid=uid) == {**first, "replayed": True}
    with pytest.raises(ValueError):
        create(b, request_uid=uid, content="Different request")
    with DbSession.use(readonly=False) as db:
        db.delete(comments()[0])
    assert create(b, request_uid=uid) is None
    assert len(comments()) == 1 and comments()[0].deleted_at is not None
    b.comments.dispatch_created.assert_called_once()


@pytest.mark.parametrize("mutation", ["foreign", "deleted", "alias", "changed"])
def test_current_source_is_rechecked_on_create_and_replay(images, mutation):
    b = images
    uid = str(uuid4())
    assert create(b, request_uid=uid)
    with DbSession.use(readonly=False) as db:
        image = db.exec(SqlBuilder.select.table(CardAttachment)).first()
        if mutation == "foreign":
            image.card_id = b.source.id + 1
        elif mutation == "deleted":
            db.delete(image)
        elif mutation == "alias":
            image.replaced_by_id = image.id
        else:
            image.file = image.file.model_copy(update={"filename": "different-immutable-key"})
        if mutation != "deleted":
            db.update(image)
    assert create(b, request_uid=uid) is None
    assert create(b) is None
    assert len(comments()) == 1 and comments()[0].anchor == anchor(b.image)
    b.comments.dispatch_created.assert_called_once()


@pytest.mark.parametrize("mutation", ["role", "membership", "account", "private", "card_deleted"])
def test_current_authority_is_rechecked_on_create_and_replay(images, mutation):
    b = images
    uid = str(uuid4())
    assert create(b, request_uid=uid)
    with b.engine.begin() as db:
        if mutation == "role":
            db.execute(sa.update(ProjectRole).values(actions=[]))
        elif mutation == "membership":
            db.execute(sa.delete(ProjectAssignedUser))
        elif mutation == "account":
            db.execute(sa.update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif mutation == "private":
            db.execute(
                sa.update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id)
            )
        else:
            db.execute(sa.update(Card).where(Card.id == b.source.id).values(deleted_at=SafeDateTime.now()))
    assert create(b, request_uid=uid) is None
    assert create(b) is None
    assert len(comments()) == 1
    b.comments.dispatch_created.assert_called_once()


def test_concurrent_retry_commits_one_comment_and_one_effect(images):
    b = images
    uid = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(b, request_uid=uid), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert len({result["comment_uid"] for result in results}) == 1
    assert len(comments()) == 1
    b.comments.dispatch_created.assert_called_once()
    with b.engine.begin() as db:
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(
                sa.insert(CardComment.__table__).values(
                    **{
                        **{
                            column.name: getattr(comments()[0], column.name) for column in CardComment.__table__.columns
                        },
                        "id": comments()[0].id + 1,
                    }
                )
            )


def test_uuid_required_and_fingerprint_actor_bound(images):
    b = images
    for uid in (None, "bad"):
        with pytest.raises(ValueError):
            b.comments.create(
                b.actor, b.source_board, b.source, EditorContentModel(content="x"), anchor(b.image), request_uid=uid
            )
    uid = str(uuid4())
    assert create(b, request_uid=uid)
    # The board owner is also authorized; UUID cannot be reused by another actor.
    with pytest.raises(ValueError):
        create(b, request_uid=uid, actor=b.outsider)


def test_http_native_post_returns_receipt_and_preserves_plain_response(images, monkeypatch):
    from langboard.routes.board import BoardCardCommentApi as api

    b = images
    monkeypatch.setattr(api, "require_visible_card", lambda *args: b.source)
    app = FastAPI()

    @app.post("/comment")
    def post(form: CreateCardCommentForm):
        return api.add_card_comment(
            b.source_board.get_uid(),
            b.source.get_uid(),
            SimpleNamespace(scope={}),
            form,
            b.actor,
            SimpleNamespace(card_comment=b.comments),
        )

    with TestClient(app) as client:
        payload = dict(content="Review image", anchor=anchor(b.image), request_uid=str(uuid4()))
        first = client.post("/comment", json=payload)
        again = client.post("/comment", json=payload)
        assert first.status_code == again.status_code == 201
        assert first.json()["comment_uid"] == again.json()["comment_uid"]
        assert first.json()["replayed"] is False and again.json()["replayed"] is True
        assert client.post("/comment", json={**payload, "anchor": {**payload["anchor"], "x": "1"}}).status_code == 422
        plain = client.post("/comment", json={"content": "Plain"})
        assert plain.status_code == 201 and plain.json() == {}


def test_migration_preserves_text_and_refuses_receipt_loss():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022140000-3e14fa528d76.py"
    spec = spec_from_file_location("image_anchor_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(sa.text("CREATE TABLE card_comment (id INTEGER PRIMARY KEY, anchor JSON)"))
        db.execute(sa.text('INSERT INTO card_comment (id, anchor) VALUES (1, \'{"exact":"text"}\')'))
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        assert db.execute(sa.text("SELECT anchor FROM card_comment")).scalar() == '{"exact":"text"}'
        db.execute(sa.text("UPDATE card_comment SET image_request_uid='receipt'"))
        with pytest.raises(RuntimeError):
            migration.downgrade()
        db.execute(sa.text("UPDATE card_comment SET image_request_uid=NULL"))
        migration.downgrade()
        assert {c["name"] for c in sa.inspect(db).get_columns("card_comment")} == {"id", "anchor"}


def test_native_upload_same_bytes_preserve_revision_changed_bytes_replace_it(uploads):  # noqa: F811
    b = uploads
    upload(b, name="image.png", content=b"same-image-bytes")
    before = rows(CardAttachment)[0]
    revision = before.image_revision
    preview = upload(b, name="renamed.png", content=b"same-image-bytes")
    choose(b, preview, name="renamed.png", content=b"same-image-bytes")
    same = rows(CardAttachment)[0]
    assert same.get_uid() == before.get_uid() and same.image_revision == revision
    preview = upload(b, name="renamed.png", content=b"changed-image-bytes")
    choose(b, preview, name="renamed.png", content=b"changed-image-bytes")
    changed = rows(CardAttachment)[0]
    assert changed.get_uid() == before.get_uid() and changed.image_revision != revision


def test_image_create_passes_authenticated_channel_to_current_visibility(images):
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel

    b = images
    current = b.service.resolve_readable_card
    b.service.resolve_readable_card = Mock(wraps=current)
    b.comments.create(
        b.actor,
        b.source_board,
        b.source,
        EditorContentModel(content="Image"),
        anchor(b.image),
        request_uid=str(uuid4()),
        channel=CollaborationChannel.Mcp,
    )
    assert b.service.resolve_readable_card.call_args.args[-1] == CollaborationChannel.Mcp


def test_outer_rollback_discards_comment_receipt_and_effect(images):
    from langboard_shared.domain.services.CardChangeTransaction import dispatch_after_commit

    b = images
    uid = str(uuid4())
    transport = Mock()
    b.comments.dispatch_created.side_effect = lambda *args: dispatch_after_commit(transport)
    with pytest.raises(RuntimeError, match="abort"):
        with DbSession.atomic():
            assert create(b, request_uid=uid)["replayed"] is False
            b.comments.dispatch_created.assert_called_once()
            transport.assert_not_called()
            raise RuntimeError("abort")
    assert comments() == []
    transport.assert_not_called()
    b.comments.dispatch_created.reset_mock()
    assert create(b, request_uid=uid)["replayed"] is False
    b.comments.dispatch_created.assert_called_once()
    transport.assert_called_once()


def test_actual_source_dimensions_reject_mismatch_without_receipt_or_effect(images):
    b = images
    uid = str(uuid4())
    assert create(b, request_uid=uid, image_anchor={**anchor(b.image), "image_width": 1919}) is None
    assert comments() == []
    b.comments.dispatch_created.assert_not_called()
    first = create(b, request_uid=uid)
    assert first["replayed"] is False
    assert create(b, request_uid=uid) == {**first, "replayed": True}
    assert len(comments()) == 1
    b.comments.dispatch_created.assert_called_once()


@pytest.mark.parametrize("kind", ["unsupported", "truncated", "pixel_limit", "byte_limit", "missing"])
def test_invalid_source_bytes_rejected_without_side_effect(images, monkeypatch, kind):
    from langboard_shared.domain.services import CardCommentImageAnchors as native

    b = images
    if kind == "unsupported":
        b.image_bytes = b"<svg xmlns='http://www.w3.org/2000/svg' width='1920' height='1080'/>"
    elif kind == "truncated":
        b.image_bytes = b"\x89PNG\r\n\x1a\nfixture"
    elif kind == "pixel_limit":
        monkeypatch.setattr(native, "MAX_IMAGE_PIXELS", 100)
    elif kind == "byte_limit":
        monkeypatch.setattr(native, "MAX_IMAGE_BYTES", len(b.image_bytes) - 1)
    else:
        b.download.side_effect = lambda *args: False
    assert create(b) is None
    assert comments() == []
    b.comments.dispatch_created.assert_not_called()
    b.service.mark_card_changed.assert_not_called()


@pytest.mark.parametrize("format", ["PNG", "JPEG", "GIF", "WEBP"])
def test_supported_source_formats_use_real_intrinsic_dimensions(images, format):
    b = images
    output = BytesIO()
    Image.new("RGB", (40, 30)).save(output, format=format)
    b.image_bytes = output.getvalue()
    value = {**anchor(b.image), "image_width": 40, "image_height": 30}
    assert create(b, image_anchor=value)["replayed"] is False
    assert comments()[0].anchor == value


@pytest.mark.parametrize("mutation", ["source", "role"])
def test_storage_download_revalidates_source_and_access(images, mutation):
    b = images
    download = b.download.side_effect

    def mutate(*args):
        found = download(*args)
        with DbSession.use(readonly=False) as db:
            if mutation == "source":
                image = db.exec(SqlBuilder.select.table(CardAttachment)).first()
                image.file = image.file.model_copy(update={"filename": "replacement-key"})
                db.update(image)
            else:
                db.exec(sa.update(ProjectRole).values(actions=[]))
        return found

    b.download.side_effect = mutate
    assert create(b) is None
    assert comments() == []
    b.comments.dispatch_created.assert_not_called()


@pytest.mark.parametrize("orientation", [1, 3, 6, 8])
def test_exif_oriented_jpeg_matches_browser_natural_dimensions(images, orientation):
    b = images
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", (40, 30)).save(output, format="JPEG", exif=exif)
    b.image_bytes = output.getvalue()
    width, height = (30, 40) if orientation in {6, 8} else (40, 30)
    value = {**anchor(b.image), "image_width": width, "image_height": height}
    assert create(b, image_anchor=value)["replayed"] is False
    assert comments()[0].anchor == value


def test_dimension_validation_does_not_decode_raster(images, monkeypatch):
    from PIL import PngImagePlugin

    def forbidden(*args):
        pytest.fail("Dimension validation must not decode raster pixels")

    monkeypatch.setattr(Image.Image, "load", forbidden)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", forbidden)
    assert create(images)["replayed"] is False
