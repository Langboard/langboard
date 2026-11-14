"""Native PostgreSQL image comment authority and edit/FK lock interleaving."""

import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, CardAttachment, CardChangeReceipt, CardComment, ProjectRole
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.domain.services.factory.CardService import CardService
from PIL import Image
from sqlalchemy import event, func, select, update
from src.api.tests.card_workspace.test_work_plan_native_edit_concurrency import native_edit_board as native_edit_board


@pytest.fixture
def image_edit_board(native_edit_board, monkeypatch):  # noqa: F811 - imported native fixture
    f = native_edit_board
    output = BytesIO()
    Image.new("RGB", (40, 30)).save(output, format="PNG")

    def download(_storage, _name, _filename, destination):
        destination.write(output.getvalue())
        return True

    monkeypatch.setattr(Storage, "_download", download)
    with DbSession.atomic() as db:
        f.image = CardAttachment(
            card_id=f.card.id,
            user_id=f.user.id,
            filename="image.png",
            file=FileModel(
                filename="immutable-image",
                original_filename="image.png",
                storage_name="card_attachment",
                storage_type="local",
                path="/image.png",
            ),
        )
        db.insert(f.image)
    f.comments = f.get(CardCommentService)
    f.anchor = dict(
        type="ImageRegionSelector",
        version=1,
        shape="point",
        image_width=40,
        image_height=30,
        x=10,
        y=20,
        width=0,
        height=0,
        source=dict(type="card_attachment", attachment_uid=f.image.get_uid(), revision=f.image.image_revision),
    )
    f.request_uid = str(uuid4())
    return f


def create(f):
    return f.comments.create(
        f.user,
        f.project,
        f.card,
        EditorContentModel(content="Image evidence"),
        f.anchor,
        request_uid=f.request_uid,
        dispatch_effects=False,
    )


@pytest.mark.parametrize("operation", ["create", "replay"])
def test_native_edit_and_image_comment_both_commit(image_edit_board, monkeypatch, operation):
    f = image_edit_board
    if operation == "replay":
        assert create(f)["replayed"] is False
    card_locked, project_locked = threading.Event(), threading.Event()
    worker = threading.local()
    original_mark = CardService.mark_card_changed

    def mark_changed(service, card, *args, **kwargs):
        if getattr(worker, "role", None) == "editor":
            card_locked.set()
            assert project_locked.wait(5), "Image comment never acquired its project guard"
        return original_mark(service, card, *args, **kwargs)

    def after_sql(_connection, _cursor, statement, _parameters, _context, _many):
        if getattr(worker, "role", None) == "image" and "FROM project" in statement and "FOR " in statement:
            project_locked.set()

    monkeypatch.setattr(CardService, "mark_card_changed", mark_changed)
    event.listen(f.engine, "after_cursor_execute", after_sql)

    def edit():
        worker.role = "editor"
        return f.cards.update(f.user, f.project, f.card, {"title": "Native concurrent image edit"})

    def annotate():
        worker.role = "image"
        return create(f)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(edit)
            assert card_locked.wait(5), "Native edit never acquired the card row"
            annotation = pool.submit(annotate)
            assert editor.result(timeout=15)["title"] == "Native concurrent image edit"
            result = annotation.result(timeout=15)
    finally:
        event.remove(f.engine, "after_cursor_execute", after_sql)
    assert result["replayed"] is (operation == "replay")
    assert create(f) == {**result, "replayed": True}
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == 1
        assert db.execute(select(CardComment.anchor)).scalar_one() == f.anchor
        assert db.execute(select(func.count()).select_from(CardChangeReceipt)).scalar_one() == 2
        assert db.execute(select(Card.title).where(Card.id == f.card.id)).scalar_one() == "Native concurrent image edit"


@pytest.mark.parametrize("mutation", ["role", "source"])
def test_image_replay_rechecks_native_authority_and_version(image_edit_board, mutation):
    f = image_edit_board
    assert create(f)["replayed"] is False
    with f.engine.begin() as db:
        if mutation == "role":
            db.execute(update(ProjectRole).where(ProjectRole.user_id == f.user.id).values(actions=[]))
        else:
            replacement = f.image.file.model_copy(update={"filename": "replacement-image"})
            db.execute(update(CardAttachment).where(CardAttachment.id == f.image.id).values(file=replacement))
    assert create(f) is None
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(CardChangeReceipt)).scalar_one() == 1


@pytest.mark.parametrize("shape", ["point", "rectangle"])
@pytest.mark.parametrize("mutation", ["source", "alias", "comment_deleted", "role"])
def test_image_replay_refreshes_rows_cached_by_outer_transaction(image_edit_board, shape, mutation):
    f = image_edit_board
    if shape == "rectangle":
        f.anchor.update(shape="rectangle", width=15, height=5)
    assert create(f)["replayed"] is False
    with DbSession.atomic() as db:
        # Keep strong references: the native session identity map can retain rows
        # read by the caller before another transaction commits a replacement.
        image = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == f.image.id)).first()
        comment = db.exec(SqlBuilder.select.table(CardComment)).first()
        role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.user_id == f.user.id)).first()
        with f.engine.begin() as concurrent:
            if mutation == "source":
                concurrent.execute(
                    update(CardAttachment).where(CardAttachment.id == image.id).values(
                        file=image.file.model_copy(update={"filename": "replacement-image"})
                    )
                )
            elif mutation == "alias":
                concurrent.execute(
                    update(CardAttachment).where(CardAttachment.id == image.id).values(replaced_by_id=image.id)
                )
            elif mutation == "comment_deleted":
                concurrent.execute(
                    update(CardComment).where(CardComment.id == comment.id).values(deleted_at=SafeDateTime.now())
                )
            else:
                concurrent.execute(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=[]))
        assert create(f) is None
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == 1
        assert db.execute(select(CardComment.anchor)).scalar_one() == f.anchor
        assert db.execute(select(func.count()).select_from(CardChangeReceipt)).scalar_one() == 1
