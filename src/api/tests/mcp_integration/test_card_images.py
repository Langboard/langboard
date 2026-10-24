"""Native image results retain card ancestry, content boundaries and budgets."""

from io import BytesIO
from types import SimpleNamespace
import pytest
from fastmcp.tools import ToolResult
from langboard.mcp_integration.ResponseBudget import ReadResponseBudgetMiddleware
from langboard.mcp_tools import CardImagesMcp as images
from mcp.types import ImageContent
from PIL import Image


@pytest.fixture
def image_card(monkeypatch):
    monkeypatch.setattr(
        images, "Env", SimpleNamespace(PUBLIC_UI_URL="https://board.example", API_URL="https://board.example/api")
    )
    card = SimpleNamespace(id=1, description=SimpleNamespace(content="![body](/api/file/key/card_attachment/a.png)"))

    def row(uid, card_id, filename):
        return SimpleNamespace(
            card_id=card_id,
            replaced_by_id=None,
            get_uid=lambda: uid,
            image_revision="a" * 64,
            file=SimpleNamespace(
                path=f"/file/key/card_attachment/{filename}",
                storage_type="key",
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

    def download(storage, name, filename, destination):
        calls.append((storage, name, filename))
        destination.write(service.image_bytes)
        return True

    monkeypatch.setattr(images.Storage, "download", download)
    return service, calls


def test_native_images_return_content_deduplicate_and_reject_foreign_attachment(image_card):
    service, calls = image_card
    result = images.read_card_images("project", "card", object(), service, ["valid", "foreign", "missing"])
    assert [block.type for block in result.content] == ["text", "image"]
    assert result.content[1].mime_type == "image/png"
    assert calls == [("key", "card_attachment", "a.png")]
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
        SimpleNamespace(block_type="rich_text", payload={"text": "![image](/file/key/card_attachment/block.png)"}),
        SimpleNamespace(block_type="code", payload={"source": "![image](/file/key/card_attachment/code.png)"}),
        SimpleNamespace(block_type="diagram", payload={"source": '<img src="/file/key/card_attachment/diagram.png">'}),
    ]
    result = images.read_card_images("project", "card", object(), service)
    assert calls == [("key", "card_attachment", "block.png")]
    assert len(result.structured_content["included"]) == 1


def test_code_only_blocks_do_not_revive_legacy_body(image_card):
    service, calls = image_card
    service.card_content_block.get_blocks_by_card = lambda card: [
        SimpleNamespace(block_type="code", payload={"source": "![image](/file/key/card_attachment/code.png)"})
    ]
    result = images.read_card_images("project", "card", object(), service)
    assert calls == [] and result.structured_content["included"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://foreign.example/file/key/card_attachment/a.png",
        "file:///etc/passwd",
        "/file/key/card_attachment/../secret",
        "/file/key/card_attachment/%2e%2e",
        "/file/key/avatar/a.png",
        "/file/key/card_attachment/a.png?token=x",
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
        "![a](/file/key/card_attachment/a.png) ![b](/file/key/card_attachment/b.png)"
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
    service.source_card.description.content = "![foreign](/file/key/card_attachment/foreign.png)"
    result = images.read_card_images("p", "c", object(), service)
    assert calls == []
    assert result.structured_content["included"] == []
    assert result.structured_content["omitted"][0]["reason"] == "attachment_not_in_card"


def test_old_alias_cannot_download_current_replacement_bytes(image_card):
    service, calls = image_card
    service.source_card.description.content = "![old](/file/key/card_attachment/old.png)"
    current = service.card_attachment.get_by_id_like("valid")
    service.card_attachment.resolve_file_owner = lambda *args: current
    result = images.read_card_images("p", "c", object(), service)
    assert calls == [] and result.structured_content["included"] == []


def test_authority_revoked_during_download_discards_image(image_card, monkeypatch):
    service, calls = image_card
    original = images.Storage.download

    def download(*args):
        result = original(*args)
        service.card.resolve_readable_card = lambda *args: None
        return result

    monkeypatch.setattr(images.Storage, "download", download)
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
