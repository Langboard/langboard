"""Current native Cloud hyperlink authorization and bounded continuation."""

from json import dumps
import pytest
from langboard.apps import BoardCloudLinks as links
from langboard.apps.BoardCloud import read_board_cloud
from langboard_shared.core.db import EditorContentModel
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, ProjectAssignedUser, ProjectRole, ProjectWiki, ProjectWikiAssignedUser
from sqlalchemy import delete, event, update


pytest_plugins = ["src.api.tests.card_visibility.test_board_cloud"]


def read(fixture, **kwargs):
    return read_board_cloud(
        fixture.service,
        fixture.actor,
        fixture.project.get_uid(),
        channel=CollaborationChannel.HumanUI,
        view="links",
        **kwargs,
    )


def body(fixture, text, *, wiki=False):
    model, field, identifier = (
        (ProjectWiki, "content", fixture.wiki.id) if wiki else (Card, "description", fixture.card.id)
    )
    with fixture.engine.begin() as db:
        db.execute(update(model).where(model.id == identifier).values(**{field: EditorContentModel(content=text)}))


def test_actual_markdown_html_dedup_and_source_provenance(cloud):
    body(
        cloud,
        "[Spec](https://example.org/spec) [Again](https://example.org/spec)\n"
        '<a href="https://example.org/html">HTML</a>\n<https://example.org/auto>\n'
        "![not a hyperlink](https://example.org/image.png)\n`[code](https://example.org/code)`\n",
    )
    body(cloud, "[Wiki spec](https://example.org/spec)", wiki=True)
    data = read(cloud)
    assert len(data["items"]) == 4
    assert len({item["id"] for item in data["items"]}) == 4
    assert [
        item["name"] for item in data["items"] if item["source"].get("card_uid") and item["url"].endswith("/spec")
    ] == ["Spec"]
    assert {item["source"]["url"] for item in data["items"]} == {
        f"/board/{cloud.project.get_uid()}/{cloud.card.get_uid()}",
        f"/board/{cloud.project.get_uid()}/wiki/{cloud.wiki.get_uid()}",
    }
    assert data["capabilities"]["hyperlinks"] and data["capabilities"]["all_recent"] == "attachments_only"
    assert data["limitation_count"] == 0 and data["total"] is None and not data["total_is_exact"]


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,secret",
        "file:///Users/private",
        "/local/path",
        "//example.org/a",
        "https://user:secret@example.org/a",
        "https://user@example.org/a",
        "https://example.org:bad/a",
        "https://example.org/\nsecret",
        "https://example.org\\@evil.invalid/a",
    ],
)
def test_unsafe_links_rejected_without_fetch(value):
    assert links.safe_link(value) is None


@pytest.mark.parametrize("denial", ["private_card", "deleted_card", "linked_card", "private_wiki"])
def test_current_source_acl_never_returns_title_or_url(cloud, denial):
    body(cloud, "[Sensitive card](https://private.invalid/card)")
    body(cloud, "[Sensitive wiki](https://private.invalid/wiki)", wiki=True)
    with cloud.engine.begin() as db:
        if denial == "private_card":
            db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
        elif denial == "deleted_card":
            db.execute(update(Card).values(deleted_at=SafeDateTime.now()))
        elif denial == "linked_card":
            db.execute(update(Card).values(source_type="project_card", source_uid="mirror"))
        else:
            db.execute(delete(ProjectWikiAssignedUser))
    data = read(cloud)
    denied = "wiki" if denial == "private_wiki" else "card"
    assert all(not item["url"].endswith(denied) for item in data["items"])
    assert ("Sensitive wiki" if denied == "wiki" else "Sensitive card") not in dumps(data)


@pytest.mark.parametrize("denial", ["role", "membership"])
def test_current_board_read_and_membership_required(cloud, denial):
    with cloud.engine.begin() as db:
        db.execute(delete(ProjectRole if denial == "role" else ProjectAssignedUser))
    with pytest.raises(ApiException.NotFound_404):
        read(cloud)


def test_long_source_preserves_all_links_across_scoped_pages(cloud):
    body(cloud, "\n".join(f"[Link {index}](https://example.org/{index})" for index in range(211)))
    first = read(cloud, limit=13)
    items, page = list(first["items"]), first
    while page["has_more"]:
        page = read(cloud, limit=13, cursor=page["next_cursor"])
        assert len(dumps(page, ensure_ascii=False).encode()) <= links.MAX_PAGE_BYTES
        items.extend(page["items"])
    assert len(items) == 211 and len({item["id"] for item in items}) == 211
    assert [item["url"] for item in items] == [f"https://example.org/{index}" for index in range(211)]
    assert read(cloud, limit=13)["items"] == first["items"]
    with pytest.raises(ApiException.BadRequest_400):
        read_board_cloud(
            cloud.service,
            cloud.actor,
            cloud.project.get_uid(),
            channel=CollaborationChannel.Mcp,
            view="links",
            cursor=first["next_cursor"],
        )


def test_source_change_rejects_stale_offset_and_revocation_skips_pending_source(cloud):
    body(cloud, "\n".join(f"[Link](https://example.org/{index})" for index in range(5)))
    first = read(cloud, limit=1)
    body(cloud, "[Replacement](https://example.org/replacement)")
    with pytest.raises(ApiException.BadRequest_400):
        read(cloud, cursor=first["next_cursor"])
    with cloud.engine.begin() as db:
        db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
    data = read(cloud, cursor=first["next_cursor"])
    assert not data["items"] and not data["has_more"]
    assert "replacement" not in dumps(data)


def test_oversized_source_reports_accurate_limitation_and_sql_bounded_body(cloud, monkeypatch):
    body(cloud, "x" * 2000 + "[Not claimed](https://example.org/end)")
    monkeypatch.setattr(links, "MAX_SOURCE_STORAGE_CHARS", 1000)
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(cloud.engine, "before_cursor_execute", observed)
    try:
        data = read(cloud)
    finally:
        event.remove(cloud.engine, "before_cursor_execute", observed)
    assert not data["items"] and not data["has_more"]
    assert data["limitation_count"] == 1
    assert data["limitations"][0]["reason"] == "source_too_large"
    assert data["limitations"][0]["source"]["card_uid"] == cloud.card.get_uid()
    assert any("CASE WHEN" in statement and "CAST(card.description" in statement for statement in statements)
    assert all("card.description," not in statement for statement in statements)


def test_response_byte_budget_continues_inside_source(cloud):
    urls = [f"https://example.org/{index}/" + "x" * 1800 for index in range(80)]
    body(cloud, "\n".join(f"[Long {index}]({url})" for index, url in enumerate(urls)))
    page, items = read(cloud), []
    assert page["has_more"] and len(page["items"]) < 80
    for _ in range(10):
        assert len(dumps(page, ensure_ascii=False).encode()) <= links.MAX_PAGE_BYTES
        items.extend(page["items"])
        if not page["has_more"]:
            break
        page = read(cloud, cursor=page["next_cursor"])
    assert [item["url"] for item in items] == urls


def test_concurrent_body_growth_respects_request_budget_without_losing_source(cloud, monkeypatch):
    body(cloud, "[Card](https://example.org/card)" + "x" * 300)
    body(cloud, "[Wiki](https://example.org/wiki)" + "x" * 300, wiki=True)
    monkeypatch.setattr(links, "MAX_REQUEST_STORAGE_CHARS", 1000)
    monkeypatch.setattr(links, "MAX_SOURCE_STORAGE_CHARS", 800)
    original = links._read_source
    reads = []

    def grow_before_read(*args, **kwargs):
        kind = args[3]
        if len(reads) == 1:
            body(cloud, f"[Grown](https://example.org/{kind})" + "x" * 650, wiki=kind == "wiki")
        result = original(*args, **kwargs)
        reads.append(result)
        return result

    monkeypatch.setattr(links, "_read_source", grow_before_read)
    first = read(cloud)
    assert len(first["items"]) == 1 and first["has_more"]
    assert len(reads) == 2 and reads[1][4] is None
    assert sum(len(row[4] or "") for row in reads) <= links.MAX_REQUEST_STORAGE_CHARS
    second = read(cloud, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and not second["has_more"]
    assert {item["url"] for item in first["items"] + second["items"]} == {
        "https://example.org/card", "https://example.org/wiki",
    }
    assert not first["limitations"] and not second["limitations"]


@pytest.mark.parametrize("denial", ["membership", "role", "account", "internal", "wiki_assignment", "card_owner"])
def test_mid_request_revocation_rechecks_all_emitted_sources(cloud, monkeypatch, denial):
    from langboard_shared.domain.models import User

    body(cloud, "[Sensitive](https://private.invalid/card)")
    body(cloud, "[Sensitive wiki](https://private.invalid/wiki)", wiki=True)
    if denial == "internal":
        with cloud.engine.begin() as db:
            db.execute(update(Card).values(visibility="INTERNAL", owner_user_id=None))
        monkeypatch.setattr(cloud.service.card, "_resolve_internal_access", lambda _: True)
    original = links._read_source

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        if denial == "internal":
            monkeypatch.setattr(cloud.service.card, "_resolve_internal_access", lambda _: False)
        else:
            with cloud.engine.begin() as db:
                if denial == "membership":
                    db.execute(delete(ProjectAssignedUser))
                elif denial == "role":
                    db.execute(delete(ProjectRole))
                elif denial == "account":
                    db.execute(update(User).where(User.id == cloud.actor.id).values(deleted_at=SafeDateTime.now()))
                elif denial == "wiki_assignment":
                    db.execute(delete(ProjectWikiAssignedUser))
                else:
                    db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
        return result

    monkeypatch.setattr(links, "_read_source", revoked)
    if denial in {"membership", "role", "account"}:
        with pytest.raises(ApiException.NotFound_404):
            read(cloud)
    else:
        data = read(cloud)
        forbidden = "wiki" if denial == "wiki_assignment" else "card"
        assert all(not item["url"].endswith(forbidden) for item in data["items"])
