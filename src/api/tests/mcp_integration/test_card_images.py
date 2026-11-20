"""Native image results retain card ancestry, content boundaries and budgets."""

from io import BytesIO
from types import SimpleNamespace
import pytest
from fastmcp.tools import ToolResult
from langboard.mcp_integration.ResponseBudget import ReadResponseBudgetMiddleware
from langboard.mcp_tools import CardImagesMcp as images
from langboard_shared.core.utils.Encryptor import Encryptor
from mcp.types import ImageContent
from PIL import Image


STORAGE_TOKEN = Encryptor.encrypt("local", "storage_type")


@pytest.fixture
def image_card(monkeypatch):
    monkeypatch.setattr(
        images, "Env", SimpleNamespace(PUBLIC_UI_URL="https://board.example", API_URL="https://board.example/api")
    )
    card = SimpleNamespace(
        id=1, description=SimpleNamespace(content=f"![body](/api/file/{STORAGE_TOKEN}/card_attachment/a.png)")
    )

    def row(uid, card_id, filename):
        return SimpleNamespace(
            card_id=card_id,
            replaced_by_id=None,
            get_uid=lambda: uid,
            image_revision="a" * 64,
            file=SimpleNamespace(
                path=f"/file/{STORAGE_TOKEN}/card_attachment/{filename}",
                storage_type="local",
                storage_name="card_attachment",
                filename=filename,
            ),
        )

    rows = {
        "valid": row("valid", 1, "a.png"),
        "foreign": row("foreign", 2, "foreign.png"),
        "block": row("block", 1, "block.png"),
        "b": row("b", 1, "b.png"),
    }
    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_readable_card=lambda *args: (object(), card, object()),
            get_details=lambda *args: pytest.fail("Image reads must not load the complete card"),
        ),
        source_card=card,
        card_content_block=SimpleNamespace(get_blocks_by_card=lambda card: []),
        card_attachment=SimpleNamespace(
            get_by_id_like=lambda uid, **kwargs: rows.get(uid),
            resolve_file_owner=lambda *identity: next(
                (r for r in rows.values() if (r.file.storage_type, r.file.storage_name, r.file.filename) == identity),
                None,
            ),
        ),
    )
    calls = []
    output = BytesIO()
    Image.new("RGB", (40, 30)).save(output, format="PNG")
    service.image_bytes = output.getvalue()

    def download(file, destination):
        calls.append((file.storage_type, file.storage_name, file.filename))
        destination.write(service.image_bytes)
        return True

    monkeypatch.setattr(images.Storage, "download_file", download)
    return service, calls


def test_native_images_return_content_deduplicate_and_reject_foreign_attachment(image_card):
    service, calls = image_card
    result = images.read_card_images("project", "card", object(), service, ["valid", "foreign", "missing"])
    assert [block.type for block in result.content] == ["text", "image"]
    assert result.content[1].mime_type == "image/png"
    assert calls == [("local", "card_attachment", "a.png")]
    assert len(result.structured_content["omitted"]) == 2
    assert result.structured_content["total_bytes"] == len(service.image_bytes)
    assert result.structured_content["included"][0]["image_width"] == 40
    assert result.structured_content["included"][0]["image_height"] == 30


def test_attachment_only_does_not_read_body_image(image_card):
    service, calls = image_card
    service.card_content_block.get_blocks_by_card = lambda card: pytest.fail("Attachment-only must not query blocks")
    result = images.read_card_images("project", "card", object(), service, [], False)
    assert result.structured_content["included"] == [] and calls == []


def test_blocks_own_body_and_only_rich_text_images_are_read(image_card):
    service, calls = image_card
    service.card_content_block.get_blocks_by_card = lambda card: [
        SimpleNamespace(
            block_type="rich_text", payload={"text": f"![image](/file/{STORAGE_TOKEN}/card_attachment/block.png)"}
        ),
        SimpleNamespace(
            block_type="code", payload={"source": f"![image](/file/{STORAGE_TOKEN}/card_attachment/code.png)"}
        ),
        SimpleNamespace(
            block_type="diagram", payload={"source": f'<img src="/file/{STORAGE_TOKEN}/card_attachment/diagram.png">'}
        ),
    ]
    result = images.read_card_images("project", "card", object(), service)
    assert calls == [("local", "card_attachment", "block.png")]
    assert len(result.structured_content["included"]) == 1


def test_code_only_blocks_do_not_revive_legacy_body(image_card):
    service, calls = image_card
    service.card_content_block.get_blocks_by_card = lambda card: [
        SimpleNamespace(
            block_type="code", payload={"source": f"![image](/file/{STORAGE_TOKEN}/card_attachment/code.png)"}
        )
    ]
    result = images.read_card_images("project", "card", object(), service)
    assert calls == [] and result.structured_content["included"] == []


@pytest.mark.parametrize(
    "url",
    [
        f"https://foreign.example/file/{STORAGE_TOKEN}/card_attachment/a.png",
        "file:///etc/passwd",
        "/file/local/card_attachment/a.png",
        "/file/key/card_attachment/a.png",
        "/file/invalid!/card_attachment/a.png",
        f"/file/{STORAGE_TOKEN}/card_attachment/../secret",
        f"/file/{STORAGE_TOKEN}/card_attachment/%2e%2e",
        f"/file/{STORAGE_TOKEN}/avatar/a.png",
        f"/file/{STORAGE_TOKEN}/card_attachment/a.png?token=x",
    ],
)
def test_body_source_cannot_fetch_arbitrary_url_or_traverse_storage(image_card, url):
    assert images._stored_image_path(url) is None


def test_editor_parser_ignores_code_and_links():
    assert images._image_urls("`![code](/file/a)`\n[link](/file/b)\n![image](/file/c)") == ["/file/c"]
    assert images._image_urls({"children": [{"type": "img", "url": "/file/a"}]}) == ["/file/a"]
    assert images._image_urls('{"content":"[{\\"type\\":\\"img\\",\\"url\\":\\"/file/a\\"}]"}') == ["/file/a"]


def test_count_and_byte_limits_report_omission(image_card, monkeypatch):
    service, calls = image_card
    service.source_card.description.content = (
        f"![a](/file/{STORAGE_TOKEN}/card_attachment/a.png) ![b](/file/{STORAGE_TOKEN}/card_attachment/b.png)"
    )
    monkeypatch.setattr(images, "MAX_IMAGES", 1)
    result = images.read_card_images("p", "c", object(), service)
    assert result.structured_content["omitted"][0]["reason"] == "image_count_limit"
    assert len(calls) == 1
    monkeypatch.setattr(images, "MAX_TOTAL_BYTES", 4)
    result = images.read_card_images("p", "c", object(), service)
    assert {entry["reason"] for entry in result.structured_content["omitted"]} == {"byte_limit"}
    assert result.structured_content["total_bytes"] == 0


async def test_image_budget_preserves_multimodal_content_but_keeps_plain_reads_bounded():
    result = ToolResult(content=[ImageContent(type="image", data="A" * 1_100_000, mime_type="image/png")])

    async def next_call(context):
        return result

    middleware = ReadResponseBudgetMiddleware()
    image_context = SimpleNamespace(message=SimpleNamespace(name="read_card_images"))
    assert await middleware.on_call_tool(image_context, next_call) is result
    plain_context = SimpleNamespace(message=SimpleNamespace(name="read_card_attachment"))
    assert (await middleware.on_call_tool(plain_context, next_call)).is_error


def test_foreign_body_reference_never_downloads_bytes(image_card):
    service, calls = image_card
    service.source_card.description.content = f"![foreign](/file/{STORAGE_TOKEN}/card_attachment/foreign.png)"
    result = images.read_card_images("p", "c", object(), service)
    assert calls == []
    assert result.structured_content["included"] == []
    assert result.structured_content["omitted"][0]["reason"] == "attachment_not_in_card"


def test_old_alias_cannot_download_current_replacement_bytes(image_card):
    service, calls = image_card
    service.source_card.description.content = f"![old](/file/{STORAGE_TOKEN}/card_attachment/old.png)"
    current = service.card_attachment.get_by_id_like("valid")
    service.card_attachment.resolve_file_owner = lambda *args: current
    result = images.read_card_images("p", "c", object(), service)
    assert calls == [] and result.structured_content["included"] == []


def test_authority_revoked_during_download_discards_image(image_card, monkeypatch):
    service, calls = image_card
    original = images.Storage.download_file

    def download(*args):
        result = original(*args)
        service.card.resolve_readable_card = lambda *args: None
        return result

    monkeypatch.setattr(images.Storage, "download_file", download)
    result = images.read_card_images("p", "c", object(), service)
    assert len(calls) == 1 and result.structured_content["included"] == []
    assert result.structured_content["total_bytes"] == 0
    assert result.structured_content["omitted"][0]["reason"] == "source_changed"


@pytest.mark.parametrize(
    "payload, reason",
    [
        (b"\x89PNG\r\n\x1a\nfixture", "unsupported_type"),
        (b"<svg width='40' height='30'/>", "unsupported_type"),
    ],
)
def test_invalid_or_unsupported_image_is_reported(image_card, payload, reason):
    service, calls = image_card
    service.image_bytes = payload
    result = images.read_card_images("p", "c", object(), service)
    assert len(calls) == 1
    assert result.structured_content["included"] == []
    assert result.structured_content["omitted"][0]["reason"] == reason


@pytest.mark.parametrize(
    "mutation",
    [
        "unchanged",
        "foreign",
        "deleted",
        "replaced",
        "private",
        "deleted_during_download",
        "private_during_download",
        "replaced_during_download",
    ],
)
def test_encrypted_upload_url_uses_native_current_owner_and_transport(board, monkeypatch, tmp_path, mutation):
    """Exercise real upload locators, database ACL/provenance and storage transport."""
    import base64
    from langboard_shared.core.db import DbSession, EditorContentModel
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.storage import StorageName
    from langboard_shared.core.storage.LocalStorage import LocalStorage
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card, CardAttachment, CardContentBlock, EmployeeMembershipPolicy
    from langboard_shared.domain.models.AttachmentStorageObject import (
        AttachmentStorageObject,
        AttachmentStorageReference,
    )
    from langboard_shared.domain.services import DomainService
    from langboard_shared.Env import Env
    from sqlalchemy import update

    _, actor, project, _, _, columns, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (
        Card,
        CardAttachment,
        CardContentBlock,
        EmployeeMembershipPolicy,
        AttachmentStorageObject,
        AttachmentStorageReference,
    ):
        model.__table__.create(engine, checkfirst=True)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "storage"))
    monkeypatch.setattr(
        images, "Env", SimpleNamespace(PUBLIC_UI_URL="https://board.example", API_URL="https://board.example/api")
    )
    output = BytesIO()
    Image.new("RGB", (40, 30)).save(output, format="PNG")
    payload = output.getvalue()
    file = LocalStorage().upload_named(BytesIO(payload), "image.png", StorageName.CardAttachment, "original.png")
    assert file.storage_type == "local" and not file.path.startswith("/file/local/")
    with DbSession.atomic() as db:
        card = Card(
            project_id=project.id,
            project_column_id=columns[0].id,
            title="Native image",
            visibility="PRIVATE",
            owner_user_id=actor.id,
            created_by_user_id=actor.id,
            description=EditorContentModel(content=f"![body](/api{file.path})"),
        )
        db.insert(card)
        attachment = CardAttachment(card_id=card.id, user_id=actor.id, filename="image.png", file=file)
        db.insert(attachment)
    service = DomainService()
    downloads = []

    def mutate(kind):
        with DbSession.atomic() as db:
            if kind == "foreign":
                other = Card(project_id=project.id, project_column_id=columns[0].id, title="Foreign image")
                db.insert(other)
                db.exec(update(CardAttachment).where(CardAttachment.id == attachment.id).values(card_id=other.id))
            elif kind == "deleted":
                db.exec(
                    update(CardAttachment)
                    .where(CardAttachment.id == attachment.id)
                    .values(deleted_at=SafeDateTime.now())
                )
            elif kind == "replaced":
                changed = file.model_copy(
                    update={"filename": "replacement.png", "path": file.path.replace("original.png", "replacement.png")}
                )
                db.exec(update(CardAttachment).where(CardAttachment.id == attachment.id).values(file=changed))
            elif kind == "private":
                db.exec(
                    update(Card)
                    .where(Card.id == card.id)
                    .values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
                )

    def download(name, filename, destination):
        downloads.append((name, filename))
        destination.write(payload)
        if mutation.endswith("_during_download"):
            mutate(mutation.removesuffix("_during_download"))
        return True

    monkeypatch.setattr(images.Storage._storages["local"], "download", download)
    if not mutation.endswith("_during_download"):
        mutate(mutation)
    if mutation == "private":
        with pytest.raises(ValueError, match="Card not found"):
            images.read_card_images(project.get_uid(), card.get_uid(), actor, service)
        assert downloads == []
        return
    result = images.read_card_images(project.get_uid(), card.get_uid(), actor, service)
    if mutation == "unchanged":
        assert downloads == [("card_attachment", "original.png")]
        assert base64.b64decode(result.content[1].data) == payload
        included = result.structured_content["included"]
        assert included[0]["attachment_uid"] == attachment.get_uid()
        assert included[0]["image_revision"] == attachment.image_revision
        assert included[0]["image_width"] == 40
        assert included[0]["image_height"] == 30
    else:
        assert result.structured_content["included"] == []
        assert result.structured_content["total_bytes"] == 0
        assert downloads == ([("card_attachment", "original.png")] if mutation.endswith("_during_download") else [])
        assert result.structured_content["omitted"][0]["reason"] == (
            "source_changed" if mutation.endswith("_during_download") else "attachment_not_in_card"
        )
