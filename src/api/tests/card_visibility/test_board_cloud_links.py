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
pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


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
    assert data["capabilities"]["hyperlinks"] and data["capabilities"]["all_recent"] == "attachments_and_links"
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
def test_unsafe_links_rejected_without_fetch(cloud, value):
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


def read_mixed(cloud, **kwargs):
    kwargs.setdefault("channel", CollaborationChannel.HumanUI)
    return read_board_cloud(cloud.service, cloud.actor, cloud.project.get_uid(), **kwargs)


@pytest.mark.parametrize("view", ["all", "recent"])
def test_mixed_equal_timestamp_seams_and_recent20(cloud, view):
    from langboard_shared.core.db import DbSession
    from langboard_shared.domain.models import CardAttachment, ProjectAttachment, ProjectWikiAttachment

    body(cloud, "\n".join(f"[Link {index}](https://example.org/{index})" for index in range(211)))
    body(cloud, "\n".join(f"[Wiki {index}](https://wiki.example.org/{index})" for index in range(3)), wiki=True)
    with DbSession.atomic() as db:
        asset = ProjectAttachment(
            project_id=cloud.project.id, user_id=cloud.actor.id, filename="board.pdf", content_sha256="a" * 64,
            file=cloud.card_file.file.model_copy(update={"storage_name": "project_attachment", "filename": "board.pdf"}),
        )
        db.insert(asset)
    stamp = SafeDateTime.now().replace(tzinfo=None)
    with cloud.engine.begin() as db:
        for model in (Card, ProjectWiki, CardAttachment, ProjectWikiAttachment, ProjectAttachment):
            db.execute(update(model).values(created_at=stamp, updated_at=stamp))
    expected = [f"https://wiki.example.org/{index}" for index in range(3)]
    expected += ["/file/wiki", asset.file.path]
    expected += [f"https://example.org/{index}" for index in range(211)]
    expected += [row.file.path for row in sorted(
        [cloud.card_file, cloud.image, cloud.unknown], key=lambda row: int(row.id), reverse=True
    )]
    if view == "recent":
        expected = expected[:20]
    cursor, items = None, []
    for _ in range(100):
        page = read_mixed(cloud,
                                channel=CollaborationChannel.HumanUI, view=view, limit=7, cursor=cursor)
        assert len(dumps(page, ensure_ascii=False).encode()) <= links.MAX_PAGE_BYTES
        assert page["capabilities"]["all_recent"] == "attachments_and_links"
        items.extend(page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    else:
        pytest.fail("Mixed pagination failed to converge")
    assert [item["url"] for item in items] == expected
    assert len({item["id"] for item in items}) == len(expected)
    assert len({item["created_at"].split("+")[0] for item in items}) == 1


def test_mixed_newest_first_and_bounded_empty_source_pages(cloud, monkeypatch):
    from datetime import timedelta
    from langboard_shared.core.db import DbSession

    body(cloud, "[Older](https://example.org/older)")
    stamp = SafeDateTime.now() + timedelta(days=1)
    with DbSession.atomic() as db:
        for index in range(5):
            db.insert(Card(project_id=cloud.project.id, title="Empty source", visibility="SHARED",
                           created_at=stamp + timedelta(seconds=index)))
    monkeypatch.setattr(links, "MAX_SCANNED_SOURCES", 2)
    first = read_mixed(cloud, limit=3)
    assert first["items"] == [] and first["has_more"]
    items, page = [], first
    for _ in range(10):
        if not page["has_more"]:
            break
        page = read_mixed(cloud, limit=3, cursor=page["next_cursor"])
        items.extend(page["items"])
    assert not page["has_more"]
    assert len(items) == len({item["id"] for item in items}) == 5
    assert [item["created_at"] for item in items] == sorted((item["created_at"] for item in items), reverse=True)


@pytest.mark.parametrize("change", ["edit", "delete", "revoke", "linked"])
def test_mixed_pending_source_replacement_and_revocation(cloud, change):
    from datetime import timedelta

    body(cloud, "\n".join(f"[Link](https://example.org/{index})" for index in range(211)))
    with cloud.engine.begin() as db:
        db.execute(update(Card).values(created_at=SafeDateTime.now() + timedelta(days=1)))
    first = read_mixed(cloud, limit=1)
    assert first["items"][0]["kind"] == "link"
    if change == "edit":
        body(cloud, "[Replacement](https://example.org/replacement)")
        with pytest.raises(ApiException.BadRequest_400):
            read_mixed(cloud, cursor=first["next_cursor"])
        return
    with cloud.engine.begin() as db:
        values = {"deleted_at": SafeDateTime.now()} if change == "delete" else (
            {"source_type": "project_card", "source_uid": "mirror"} if change == "linked" else
            {"owner_user_id": cloud.other.id, "created_by_user_id": cloud.other.id}
        )
        db.execute(update(Card).values(**values))
    page = read_mixed(cloud, cursor=first["next_cursor"])
    assert all("card_uid" not in item["source"] for item in page["items"])
    assert not page["has_more"]


@pytest.mark.parametrize("revocation", ["wiki", "card", "board"])
def test_mixed_final_acl_rechecks_links_emitted_before_later_attachments(cloud, monkeypatch, revocation):
    from datetime import timedelta
    from langboard.apps import BoardCloud

    body(cloud, "[Secret card](https://example.org/card)")
    body(cloud, "[Secret wiki](https://example.org/wiki)", wiki=True)
    with cloud.engine.begin() as db:
        for model in (Card, ProjectWiki):
            db.execute(update(model).values(created_at=SafeDateTime.now() + timedelta(days=1)))
    original = BoardCloud._item
    applied = False

    def revoke(*args):
        nonlocal applied
        item = original(*args)
        if not applied:
            applied = True
            with cloud.engine.begin() as db:
                if revocation == "wiki":
                    db.execute(delete(ProjectWikiAssignedUser))
                elif revocation == "board":
                    db.execute(update(ProjectRole).values(actions=[]))
                else:
                    db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
        return item

    monkeypatch.setattr(BoardCloud, "_item", revoke)
    if revocation == "board":
        with pytest.raises(ApiException.NotFound_404):
            read_mixed(cloud)
    else:
        page = read_mixed(cloud)
        assert all(f"{revocation}_uid" not in item["source"] for item in page["items"])
    assert applied


def test_mixed_cursor_has_no_content_and_rejects_cross_view(cloud):
    from json import loads
    from langboard_shared.core.utils.Encryptor import Encryptor
    from langboard_shared.Env import Env

    body(cloud, "[Sensitive label](https://example.org/sensitive)")
    page = read_mixed(cloud, limit=1)
    state = loads(Encryptor.decrypt(page["next_cursor"], Env.JWT_SECRET_KEY))
    assert set(state) == {"scope", "after", "offset", "pending", "digest", "remaining", "issued"}
    assert "Sensitive" not in dumps(state) and "https://" not in dumps(state)
    for view in ("recent", "files", "media", "links"):
        with pytest.raises(ApiException.BadRequest_400):
            read_mixed(cloud, view=view, cursor=page["next_cursor"])


@pytest.mark.parametrize("view", ["files", "media"])
def test_attachment_traversal_does_not_transfer_source_bodies(cloud, view):
    body(cloud, "x" * 200_000)
    body(cloud, "y" * 200_000, wiki=True)
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(cloud.engine, "before_cursor_execute", observed)
    try:
        page = read_mixed(cloud, view=view)
    finally:
        event.remove(cloud.engine, "before_cursor_execute", observed)
    assert page["items"]
    assert all("card.description" not in statement and "project_wiki.content" not in statement
               and "card.ai_description" not in statement for statement in statements)


def test_native_card_metadata_projection_preserves_full_default(cloud):
    from sqlalchemy import inspect

    body(cloud, "Retained native description")
    args = (cloud.project, cloud.card, cloud.actor, CollaborationChannel.HumanUI)
    full = cloud.service.card.resolve_readable_card(*args)
    metadata = cloud.service.card.resolve_readable_card(*args, metadata_only=True)
    assert full[1].description.content == "Retained native description"
    assert "description" not in inspect(full[1]).unloaded
    assert {"description", "ai_description"} <= inspect(metadata[1]).unloaded


@pytest.mark.parametrize("view", ["all", "recent"])
def test_mixed_response_budget_continues_without_missing_items(cloud, view):
    urls = [f"https://example.org/{index}/" + "x" * 1800 for index in range(80)]
    body(cloud, "\n".join(f"[Long {index}]({url})" for index, url in enumerate(urls)))
    items, page = [], read_mixed(cloud, view=view)
    for _ in range(20):
        assert len(dumps(page, ensure_ascii=False).encode()) <= links.MAX_PAGE_BYTES
        items.extend(page["items"])
        if not page["has_more"]:
            break
        page = read_mixed(cloud, view=view, cursor=page["next_cursor"])
    else:
        pytest.fail("Mixed response budget did not converge")
    assert len({item["id"] for item in items}) == len(items)
    if view == "recent":
        assert len(items) == 20
    else:
        assert len(items) == 84
        assert [item["url"] for item in items if item["kind"] == "link"] == urls


def test_mixed_request_body_budget_and_oversized_limitation(cloud, monkeypatch):
    body(cloud, "[Card](https://example.org/card)" + "x" * 300)
    body(cloud, "[Wiki](https://example.org/wiki)" + "x" * 300, wiki=True)
    monkeypatch.setattr(links, "MAX_REQUEST_STORAGE_CHARS", 1000)
    monkeypatch.setattr(links, "MAX_SOURCE_STORAGE_CHARS", 800)
    original, reads = links._read_source, []

    def grow_before_read(*args, **kwargs):
        kind = args[3]
        if len(reads) == 1:
            body(cloud, f"[Grown](https://example.org/{kind})" + "x" * 650, wiki=kind == "wiki")
        row = original(*args, **kwargs)
        reads.append(row)
        return row

    monkeypatch.setattr(links, "_read_source", grow_before_read)
    page = read_mixed(cloud)
    assert page["has_more"] and sum(len(row[4] or "") for row in reads) <= 1000
    assert len(reads) == 2 and reads[1][4] is None
    next_page = read_mixed(cloud, cursor=page["next_cursor"])
    assert {item["url"] for item in page["items"] + next_page["items"] if item["kind"] == "link"} == {
        "https://example.org/card", "https://example.org/wiki",
    }
    monkeypatch.setattr(links, "_read_source", original)
    body(cloud, "x" * 2000 + "[Too large](https://example.org/large)")
    page = read_mixed(cloud)
    limitations = list(page["limitations"])
    if page["has_more"]:
        following = read_mixed(cloud, cursor=page["next_cursor"])
        limitations.extend(following["limitations"])
    assert any(entry["source"].get("card_uid") == cloud.card.get_uid() for entry in limitations)
    assert all(item["url"] != "https://example.org/large" for item in page["items"])


def test_native_wiki_metadata_projection_preserves_full_default(cloud):
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    file = cloud.wiki_file.file
    args = (cloud.actor, file.storage_type, file.storage_name, file.filename)
    event.listen(cloud.engine, "before_cursor_execute", observed)
    try:
        assert cloud.service.project_wiki.resolve_readable_file(*args).id == cloud.wiki_file.id
        assert any("project_wiki.content" in statement for statement in statements)
        statements.clear()
        assert cloud.service.project_wiki.resolve_readable_file(*args, metadata_only=True).id == cloud.wiki_file.id
        assert all("project_wiki.content" not in statement for statement in statements)
    finally:
        event.remove(cloud.engine, "before_cursor_execute", observed)


def test_mixed_final_metadata_acl_covers_attachment_recheck_race(cloud, monkeypatch):
    from langboard.apps import BoardCloud

    original, counts = BoardCloud._read_attachment_item, {}
    applied = False

    def revoked_after_native_read(*args, **kwargs):
        nonlocal applied
        item = original(*args, **kwargs)
        if item is not None:
            counts[item["id"]] = counts.get(item["id"], 0) + 1
            if counts[item["id"]] == 2 and "card_uid" in item["source"] and not applied:
                with cloud.engine.begin() as db:
                    db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
                applied = True
        return item

    monkeypatch.setattr(BoardCloud, "_read_attachment_item", revoked_after_native_read)
    page = read_mixed(cloud)
    assert applied and page["items"]
    assert all("card_uid" not in item["source"] for item in page["items"])


@pytest.mark.parametrize("change", ["delete", "revoke"])
def test_mixed_last_pending_source_removed_terminates(cloud, change):
    from langboard_shared.domain.models import CardAttachment, ProjectWikiAttachment

    body(cloud, "\n".join(f"[Link](https://example.org/{index})" for index in range(5)))
    with cloud.engine.begin() as db:
        db.execute(delete(CardAttachment))
        db.execute(delete(ProjectWikiAttachment))
        db.execute(delete(ProjectWiki))
    first = read_mixed(cloud, limit=1)
    assert first["has_more"] and len(first["items"]) == 1
    with cloud.engine.begin() as db:
        values = {"deleted_at": SafeDateTime.now()} if change == "delete" else {
            "owner_user_id": cloud.other.id, "created_by_user_id": cloud.other.id,
        }
        db.execute(update(Card).values(**values))
    page = read_mixed(cloud, cursor=first["next_cursor"])
    assert page["items"] == [] and not page["has_more"] and page["next_cursor"] is None
