"""Real native project assets merge with source attachments without losing ACLs."""

from datetime import timedelta
from json import dumps
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.routing import ApiException
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.domain.models import (
    CardAttachment,
    Project,
    ProjectAttachment,
    ProjectRole,
    ProjectWikiAttachment,
)
from langboard_shared.Env import Env
from sqlalchemy import event, update
from src.api.tests.card_visibility.test_board_cloud import add_files, read


pytest_plugins = ["src.api.tests.card_visibility.test_board_cloud"]
pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


def asset(cloud, *, owner=None, project=None, filename="project.pdf", folder="inbox", storage="project_attachment"):
    with DbSession.use(readonly=False) as db:
        row = ProjectAttachment(
            project_id=(project or cloud.project).id,
            user_id=(owner or cloud.actor).id,
            filename=filename,
            content_sha256="a" * 64,
            folder_id=folder,
            file=FileModel(storage_type="local", storage_name=storage, filename=f"project-{filename}",
                           original_filename=filename, path=f"/file/project/{filename}"),
        )
        db.insert(row)
    return row


def project_items(data):
    return [item for item in data["items"] if item["source"].get("kind") == "project"]


def test_native_source_folder_capabilities_and_no_internal_fields(cloud):
    row = asset(cloud)
    data = read(cloud)
    item = project_items(data)[0]
    board_url = f"/board/{cloud.project.get_uid()}"
    assert item["id"] == f"project_attachment:{row.get_uid()}"
    assert item["attachment_uid"] == row.get_uid()
    assert item["source"] == dict(kind="project", project_uid=cloud.project.get_uid(),
                                  title=cloud.project.title, board_url=board_url, url=board_url)
    assert item["folder_id"] == "inbox"  # Persisted folder overrides the PDF extension.
    assert item["can_replace"] is True
    assert data["capabilities"]["upload"] is True and data["capabilities"]["replace"] is True
    assert data["capabilities"]["all_recent"] == "attachments_and_links"
    assert not ({"content_sha256", "storage_name", "storage_type", "confirmation_token"} & item.keys())
    assert len(data["items"]) == 5


def test_current_replacement_authority_and_no_cached_grants(cloud):
    asset(cloud, owner=cloud.other)
    assert project_items(read(cloud))[0]["can_replace"] is False
    with cloud.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=["read", "update"]))
    assert project_items(read(cloud))[0]["can_replace"] is True
    with cloud.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=["read"]))
    assert project_items(read(cloud))[0]["can_replace"] is False


@pytest.mark.parametrize("change", ["foreign", "deleted", "namespace", "ambiguous", "moved_after_resolve", "deleted_after_resolve", "replaced_after_resolve", "revoked_after_resolve"])
def test_exact_project_provenance_rechecked(cloud, monkeypatch, change):
    row = asset(cloud)
    with DbSession.use(readonly=False) as db:
        foreign = Project(owner_id=cloud.other.id, title="Foreign project")
        db.insert(foreign)
    if change == "foreign":
        with cloud.engine.begin() as db:
            db.execute(update(ProjectAttachment).values(project_id=foreign.id))
    elif change == "deleted":
        with cloud.engine.begin() as db:
            db.execute(update(ProjectAttachment).values(deleted_at=SafeDateTime.now()))
    elif change == "namespace":
        with cloud.engine.begin() as db:
            db.execute(update(ProjectAttachment).values(file=row.file.model_copy(update={"storage_name": "wiki"})))
    elif change == "ambiguous":
        with DbSession.use(readonly=False) as db:
            db.insert(ProjectAttachment(project_id=foreign.id, user_id=cloud.other.id, filename="secret.pdf",
                                        content_sha256="b" * 64, file=row.file))
    else:
        resolver = cloud.service.project_attachment.resolve_readable_file

        def mutate(*args):
            result = resolver(*args)
            changes = ({"project_id": foreign.id} if change == "moved_after_resolve"
                       else {"deleted_at": SafeDateTime.now()} if change == "deleted_after_resolve"
                       else {"file": row.file.model_copy(update={"filename": "replacement.pdf"})})
            with cloud.engine.begin() as db:
                if change == "revoked_after_resolve":
                    db.execute(update(ProjectRole).values(actions=[]))
                else:
                    db.execute(update(ProjectAttachment).where(ProjectAttachment.id == row.id).values(**changes))
            return result

        monkeypatch.setattr(cloud.service.project_attachment, "resolve_readable_file", mutate)
    if change == "revoked_after_resolve":
        with pytest.raises(ApiException.NotFound_404):
            read(cloud)
    else:
        assert project_items(read(cloud)) == []


def test_current_account_and_membership_revocation_between_project_pages(cloud):
    asset(cloud, filename="first.pdf")
    asset(cloud, filename="second.pdf")
    first = read(cloud, limit=1)
    assert first["has_more"]
    with cloud.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=[]))
    assert cloud.service.project_attachment.can_upload(cloud.actor, cloud.project) is False
    with pytest.raises(ApiException.NotFound_404):
        read(cloud, cursor=first["next_cursor"])


def test_three_equal_timestamp_streams_have_total_order_and_recent_limit(cloud):
    add_files(cloud, 35)
    add_files(cloud, 9, wiki=True)
    stamp = SafeDateTime.now() + timedelta(days=1)
    for i in range(7):
        asset(cloud, filename=f"asset-{i}.pdf", folder="documents")
    with cloud.engine.begin() as db:
        for model in (ProjectAttachment, CardAttachment, ProjectWikiAttachment):
            db.execute(update(model).values(created_at=stamp))
    expected = read(cloud)["items"]
    found, cursor = [], None
    for _ in range(20):
        page = read(cloud, limit=4, cursor=cursor)
        found.extend(page["items"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert [item["id"] for item in found] == [item["id"] for item in expected]
    assert len(found) == len({item["id"] for item in found}) == 55
    first = read(cloud, view="recent", limit=13)
    second = read(cloud, view="recent", limit=13, cursor=first["next_cursor"])
    assert len(first["items"]) == 13 and len(second["items"]) == 7
    assert not second["has_more"]


def test_old_cursor_scope_rejected(cloud):
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel

    row = asset(cloud)
    cursor = Encryptor.encrypt(dumps({
        "scope": ["board-cloud:1", str(cloud.actor.id), str(cloud.project.id), CollaborationChannel.HumanUI.value, "all"],
        "after": [row.created_at.isoformat(), "card", int(row.id)], "remaining": 20,
        "issued": int(__import__("time").time()),
    }), Env.JWT_SECRET_KEY)
    with pytest.raises(ApiException.BadRequest_400):
        read(cloud, cursor=cursor)


def test_project_stream_is_bounded_and_filtered_pages_continue(cloud, monkeypatch):
    row = asset(cloud)
    stamp = SafeDateTime.now() + timedelta(days=1)
    with cloud.engine.begin() as db:
        db.execute(ProjectAttachment.__table__.insert(), [dict(
            id=int(row.id) + 1000 + i, project_id=cloud.project.id, user_id=cloud.actor.id,
            filename="bulk.pdf", file=row.file.model_copy(update={"filename": f"bulk-{i}.pdf"}),
            content_sha256="a" * 64, folder_id="inbox", created_at=stamp, updated_at=stamp,
        ) for i in range(501)])
    calls, queries = [], []
    resolver = cloud.service.project_attachment.resolve_readable_file

    def tracked(*args):
        calls.append(args)
        return resolver(*args)

    def trace(connection, cursor, statement, parameters, context, executemany):
        if "FROM project_attachment" in statement and "ORDER BY" in statement:
            queries.append((statement, parameters))

    monkeypatch.setattr(cloud.service.project_attachment, "resolve_readable_file", tracked)
    event.listen(cloud.engine, "before_cursor_execute", trace)
    try:
        page = read(cloud, view="media")
    finally:
        event.remove(cloud.engine, "before_cursor_execute", trace)
    assert page["items"] == [] and page["has_more"]
    assert len(calls) == 500 and len(queries) == 3
    for statement, params in queries:
        assert "LIMIT" in statement and "deleted_at IS NULL" in statement
        assert (params["param_1"] if isinstance(params, dict) else params[-2]) <= 200
    following = read(cloud, view="media", cursor=page["next_cursor"])
    assert [item["name"] for item in following["items"]] == ["photo.png"]


@pytest.mark.parametrize("kind", ["card", "wiki"])
@pytest.mark.parametrize("change", ["deleted", "replaced"])
@pytest.mark.parametrize("view", ["all", "files"])
def test_final_native_attachment_resolution_rechecks_committed_version(cloud, monkeypatch, kind, change, view):
    model, row, owner_service, method = (
        (CardAttachment, cloud.card_file, cloud.service.card_attachment, "resolve_file_owner")
        if kind == "card" else
        (ProjectWikiAttachment, cloud.wiki_file, cloud.service.project_wiki, "resolve_readable_file")
    )
    resolver = getattr(owner_service, method)
    calls = 0

    def changed_after_resolution(*args, **kwargs):
        nonlocal calls
        owner = resolver(*args, **kwargs)
        if owner is not None and owner.id == row.id:
            calls += 1
            # First projection succeeds. Replacement/deletion overlaps the final
            # response check after native resolution returned the previous row.
            if calls == 2:
                changes = ({"deleted_at": SafeDateTime.now()} if change == "deleted" else
                           {"file": row.file.model_copy(update={"filename": "new-version.pdf"})})
                with cloud.engine.begin() as db:
                    db.execute(update(model).where(model.id == row.id).values(**changes))
        return owner

    monkeypatch.setattr(owner_service, method, changed_after_resolution)
    result = read(cloud, view=view)
    assert calls == 2
    assert f"{kind}_attachment:{row.get_uid()}" not in {item["id"] for item in result["items"]}
    assert result["items"]  # Nearby unaffected native attachments remain visible.
