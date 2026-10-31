"""Native board cloud projection preserves file provenance and source ACL."""

from types import SimpleNamespace
import pytest
from langboard.apps.BoardCloud import read_board_cloud
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    Project,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    User,
)
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.ProjectAttachmentService import ProjectAttachmentService
from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService
from sqlalchemy import delete, update


pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


@pytest.fixture
def cloud(current_card):
    actor, project, card, card_service = current_card
    engine = DbEngine.get_main_engine()
    with engine.begin() as connection:
        for model in (CardAttachment, ProjectWiki, ProjectWikiAssignedUser, ProjectWikiAttachment, ProjectAttachment):
            model.__table__.create(connection)
    with DbSession.use(readonly=False) as db:
        other = User(
            firstname="Other",
            lastname="Actor",
            email="other@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(other)
    with engine.begin() as db:
        db.execute(update(Project).values(owner_id=other.id))
    with DbSession.use(readonly=False) as db:
        db.insert(ProjectRole(project_id=project.id, user_id=actor.id, actions=["read"]))
        member = ProjectAssignedUser(project_id=project.id, user_id=actor.id)
        db.insert(member)
        wiki = ProjectWiki(project_id=project.id, title="Restricted wiki", is_public=False)
        db.insert(wiki)
        db.insert(ProjectWikiAssignedUser(project_assigned_id=member.id, project_wiki_id=wiki.id, user_id=actor.id))
        card_file = CardAttachment(
            user_id=actor.id,
            card_id=card.id,
            filename="same.pdf",
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="native-card.pdf",
                original_filename="same.pdf",
                path="/file/card",
            ),
        )
        wiki_file = ProjectWikiAttachment(
            user_id=actor.id,
            project_wiki_id=wiki.id,
            filename="same.pdf",
            file=FileModel(
                storage_type="local",
                storage_name="wiki",
                filename="native-wiki.pdf",
                original_filename="same.pdf",
                path="/file/wiki",
            ),
        )
        image = CardAttachment(
            user_id=actor.id,
            card_id=card.id,
            filename="photo.png",
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="native-image.png",
                original_filename="photo.png",
                path="/file/image",
            ),
        )
        unknown = CardAttachment(
            user_id=actor.id,
            card_id=card.id,
            filename="raw.unknown",
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="raw.unknown",
                original_filename="raw.unknown",
                path="/file/raw",
            ),
        )
        for item in (card_file, wiki_file, image, unknown):
            db.insert(item)
    service = SimpleNamespace(
        card=card_service,
        card_attachment=CardAttachmentService(lambda _: None, lambda _: None, SimpleNamespace()),
        project_attachment=ProjectAttachmentService(lambda _: None, lambda _: None, SimpleNamespace()),
        project_wiki=ProjectWikiService(lambda _: None, lambda _: None, SimpleNamespace()),
    )
    return SimpleNamespace(**locals())


def read(f, **kwargs):
    return read_board_cloud(f.service, f.actor, f.project.get_uid(), channel=CollaborationChannel.HumanUI, **kwargs)


@pytest.mark.parametrize("view", ["all", "files", "links"])
@pytest.mark.parametrize("revoked", ["role", "actor", "membership"])
def test_enclosing_transaction_rejects_cached_revoked_authority(cloud, view, revoked):
    with DbSession.atomic() as db:
        # Retain strong references: SQLAlchemy's identity map otherwise releases
        # unreferenced models and conceals the enclosing-transaction regression.
        cached_actor = db.exec(SqlBuilder.select.table(User).where(User.id == cloud.actor.id)).first()
        cached_role = db.exec(SqlBuilder.select.table(ProjectRole).where(
            ProjectRole.project_id == cloud.project.id, ProjectRole.user_id == cloud.actor.id
        )).first()
        cached_member = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(
            ProjectAssignedUser.project_id == cloud.project.id, ProjectAssignedUser.user_id == cloud.actor.id
        )).first()
        assert cached_actor.activated_at and cached_role.is_granted("read") and cached_member
        with cloud.engine.begin() as concurrent:
            if revoked == "role":
                concurrent.execute(update(ProjectRole).where(ProjectRole.id == cached_role.id).values(actions=[]))
            elif revoked == "actor":
                concurrent.execute(update(User).where(User.id == cached_actor.id).values(activated_at=None))
            else:
                concurrent.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.id == cached_member.id))
        with pytest.raises(ApiException.NotFound_404):
            read(cloud, view=view)


def test_enclosing_transaction_rejects_cached_wiki_privacy(cloud):
    with cloud.engine.begin() as concurrent:
        concurrent.execute(update(ProjectWiki).where(ProjectWiki.id == cloud.wiki.id).values(is_public=True))
        concurrent.execute(delete(ProjectWikiAssignedUser))
    with DbSession.atomic() as db:
        cached_wiki = db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == cloud.wiki.id)).first()
        assert cached_wiki.is_public
        assert len(read(cloud)["items"]) == 4
        with cloud.engine.begin() as concurrent:
            concurrent.execute(update(ProjectWiki).where(ProjectWiki.id == cloud.wiki.id).values(is_public=False))
        items = read(cloud)["items"]
        assert len(items) == 3
        assert all("wiki_uid" not in item["source"] for item in items)


def test_enclosing_transaction_refreshes_wiki_after_native_resolution(cloud, monkeypatch):
    resolver = cloud.service.project_wiki.resolve_readable_file
    resolutions = 0

    def renamed(*args, **kwargs):
        nonlocal resolutions
        attachment = resolver(*args, **kwargs)
        resolutions += 1
        with cloud.engine.begin() as concurrent:
            concurrent.execute(update(ProjectWiki).where(ProjectWiki.id == cloud.wiki.id).values(
                title=f"Current title {resolutions}"
            ))
        return attachment

    monkeypatch.setattr(cloud.service.project_wiki, "resolve_readable_file", renamed)
    with DbSession.atomic() as db:
        cached_wiki = db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == cloud.wiki.id)).first()
        assert cached_wiki.title == "Restricted wiki"
        wiki = next(item for item in read(cloud, view="files")["items"] if "wiki_uid" in item["source"])
        assert wiki["source"]["title"] == f"Current title {resolutions}"


def test_same_name_independent_sources_and_virtual_folders(cloud):
    data = read(cloud)
    assert len(data["items"]) == 4
    assert len({item["id"] for item in data["items"]}) == 4
    duplicates = [item for item in data["items"] if item["name"] == "same.pdf"]
    assert len(duplicates) == 2
    assert {item["source"]["url"] for item in duplicates} == {
        f"/board/{cloud.project.get_uid()}/{cloud.card.get_uid()}",
        f"/board/{cloud.project.get_uid()}/wiki/{cloud.wiki.get_uid()}",
    }
    assert {item["folder_id"] for item in data["items"]} == {"inbox", "documents", "media"}
    assert data["capabilities"]["hyperlinks"] is True


def test_visible_filters_pagination_and_recent_policy(cloud):
    assert len(read(cloud, view="media")["items"]) == 1
    assert len(read(cloud, view="files")["items"]) == 3
    all_items = read(cloud)["items"]
    first = read(cloud, limit=1)
    page = read(cloud, limit=1, cursor=first["next_cursor"])
    assert page["items"] == all_items[1:2]
    assert page["total"] is None and not page["total_is_exact"] and page["has_more"]
    assert read(cloud, view="recent")["recent_limit"] == 20
    assert read(cloud)["mime_type_source"] == "filename_extension"


@pytest.mark.parametrize("denial", ["role", "membership", "deleted_project", "deleted_actor"])
def test_current_primary_project_grant_required(cloud, denial):
    from langboard_shared.domain.models import Project, User

    with cloud.engine.begin() as db:
        if denial == "role":
            db.execute(update(ProjectRole).values(actions=[]))
        elif denial == "membership":
            db.execute(delete(ProjectAssignedUser))
            db.execute(update(Project).values(owner_id=cloud.other.id))
        else:
            model = Project if denial == "deleted_project" else User
            db.execute(update(model).values(deleted_at=SafeDateTime.now()))
    with pytest.raises(ApiException.NotFound_404):
        read(cloud)


@pytest.mark.parametrize(
    "denial",
    [
        "private_card",
        "internal_card",
        "private_wiki",
        "deleted_card",
        "deleted_wiki",
        "deleted_card_file",
        "deleted_wiki_file",
        "linked_card",
        "ambiguous_card_file",
        "ambiguous_wiki_file",
    ],
)
def test_hidden_sources_never_leak_names_counts_or_urls(cloud, denial, monkeypatch):
    if denial == "internal_card":
        monkeypatch.setattr(cloud.service.card, "_resolve_internal_access", lambda _: False)
    with cloud.engine.begin() as db:
        if denial == "internal_card":
            db.execute(update(Card).values(visibility="INTERNAL", owner_user_id=None))
        elif denial == "private_card":
            db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
        elif denial == "private_wiki":
            db.execute(delete(ProjectWikiAssignedUser))
        elif denial == "linked_card":
            db.execute(update(Card).values(source_type="project_wiki", source_uid=cloud.wiki.get_uid()))
        elif denial.startswith("ambiguous_"):
            item = cloud.card_file if denial == "ambiguous_card_file" else cloud.wiki_file
            model = type(item)
            fields = (
                ("user_id", "card_id", "filename", "file", "order")
                if model is CardAttachment
                else ("user_id", "project_wiki_id", "filename", "file", "order")
            )
            db.execute(model.__table__.insert().values(id=item.id + 100, **{key: getattr(item, key) for key in fields}))
        else:
            model = {
                "deleted_card": Card,
                "deleted_wiki": ProjectWiki,
                "deleted_card_file": CardAttachment,
                "deleted_wiki_file": ProjectWikiAttachment,
            }[denial]
            db.execute(update(model).values(deleted_at=SafeDateTime.now()))
    data = read(cloud)
    if denial in {"private_card", "internal_card", "deleted_card", "deleted_card_file", "linked_card"}:
        assert len(data["items"]) == 1
        assert all("card_uid" not in item["source"] for item in data["items"])
    elif denial == "ambiguous_card_file":
        assert len(data["items"]) == 3
        assert all(item["url"] != "/file/card" for item in data["items"])
    else:
        assert len(data["items"]) == 3
        assert all("wiki_uid" not in item["source"] for item in data["items"])


def test_archived_readable_source_keeps_native_link(cloud):
    with cloud.engine.begin() as db:
        db.execute(update(Card).values(archived_at=SafeDateTime.now()))
    assert len(read(cloud)["items"]) == 4


def test_api_channel_cannot_read_private_card_files(cloud):
    data = read_board_cloud(cloud.service, cloud.actor, cloud.project.get_uid(), channel=CollaborationChannel.Api)
    assert len(data["items"]) == 1
    assert all("card_uid" not in item["source"] for item in data["items"])


def test_foreign_project_and_clamped_pagination(cloud):
    with DbSession.use(readonly=False) as db:
        foreign = Project(owner_id=cloud.other.id, title="Foreign board")
        db.insert(foreign)
        foreign_card = Card(project_id=foreign.id, title="Foreign card", visibility="SHARED")
        db.insert(foreign_card)
        db.insert(
            CardAttachment(
                user_id=cloud.actor.id,
                card_id=foreign_card.id,
                filename="foreign.pdf",
                file=cloud.card_file.file.model_copy(update={"filename": "foreign.pdf"}),
            )
        )
    with pytest.raises(ApiException.NotFound_404):
        read_board_cloud(cloud.service, cloud.actor, foreign.get_uid())
    assert len(read(cloud, limit=0)["items"]) == 1
    assert len(read(cloud, limit=500)["items"]) == 4


def test_native_route_preserves_actor_channel_and_projection(cloud):
    import json
    from langboard.routes.board.BoardCloudApi import get_board_cloud

    response = get_board_cloud(
        SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.HumanUI}),
        cloud.project.get_uid(),
        view="all",
        limit=100,
        cursor=None,
        user=cloud.actor,
        service=cloud.service,
    )
    data = json.loads(response.body)
    assert response.status_code == 200
    assert len(data["items"]) == 4
    assert {item["url"] for item in data["items"]} == {"/file/card", "/file/wiki", "/file/image", "/file/raw"}


def test_current_attachment_parent_cannot_drift_into_another_source(cloud, monkeypatch):
    owner = cloud.service.card_attachment.resolve_file_owner

    def moved(*args, **kwargs):
        row = owner(*args)
        if row is not None:
            row.card_id += 99
        return row

    monkeypatch.setattr(cloud.service.card_attachment, "resolve_file_owner", moved)
    data = read(cloud)
    assert len(data["items"]) == 1
    assert all("card_uid" not in item["source"] for item in data["items"])


def test_wiki_title_and_project_are_current_after_native_resolution(cloud, monkeypatch):
    resolver = cloud.service.project_wiki.resolve_readable_file

    def renamed(*args, **kwargs):
        owner = resolver(*args, **kwargs)
        with cloud.engine.begin() as db:
            db.execute(update(ProjectWiki).values(title="Current title"))
        return owner

    monkeypatch.setattr(cloud.service.project_wiki, "resolve_readable_file", renamed)
    wiki = next(item for item in read(cloud)["items"] if "wiki_uid" in item["source"])
    assert wiki["source"]["title"] == "Current title"


def test_wiki_attachment_cannot_move_to_another_source(cloud, monkeypatch):
    resolver = cloud.service.project_wiki.resolve_readable_file

    def moved(*args, **kwargs):
        owner = resolver(*args, **kwargs)
        if owner is not None:
            owner.project_wiki_id += 99
        return owner

    monkeypatch.setattr(cloud.service.project_wiki, "resolve_readable_file", moved)
    data = read(cloud)
    assert len(data["items"]) == 3
    assert all("wiki_uid" not in item["source"] for item in data["items"])


def add_files(cloud, count, *, wiki=False):
    from datetime import timedelta

    model = ProjectWikiAttachment if wiki else CardAttachment
    source = cloud.wiki_file if wiki else cloud.card_file
    parent = "project_wiki_id" if wiki else "card_id"
    stamp = SafeDateTime.now() + timedelta(days=1)
    rows = [
        dict(
            id=int(source.id) + 1000 + i,
            user_id=cloud.actor.id,
            **{parent: getattr(source, parent)},
            filename="same.pdf",
            file=source.file.model_copy(update={"filename": f"bulk-{wiki}-{i}.pdf", "path": f"/bulk/{wiki}/{i}"}),
            order=0,
            created_at=stamp,
            updated_at=stamp,
        )
        for i in range(count)
    ]
    with cloud.engine.begin() as db:
        db.execute(model.__table__.insert(), rows)


def test_more_than_500_equal_timestamp_candidates_converge_with_stable_ids(cloud):
    add_files(cloud, 520)
    add_files(cloud, 81, wiki=True)
    ids, cursor = [], None
    for _ in range(10):
        page = read(cloud, limit=173, cursor=cursor)
        assert page["total"] is None and page["total_is_exact"] is False
        ids.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
        # Rename and processing updates must not reorder immutable traversal.
        with cloud.engine.begin() as db:
            db.execute(update(CardAttachment).values(filename="renamed.pdf", updated_at=SafeDateTime.now()))
    else:
        pytest.fail("Pagination did not converge")
    assert len(ids) == len(set(ids)) == 605
    assert {f"card_attachment:{cloud.card_file.get_uid()}", f"wiki_attachment:{cloud.wiki_file.get_uid()}"} <= set(ids)


def test_empty_filtered_page_advances_and_bounds_sql_and_resolvers(cloud, monkeypatch):
    from sqlalchemy import event

    add_files(cloud, 601)
    calls, queries = [], []
    resolver = cloud.service.card_attachment.resolve_file_owner

    def tracked(*args):
        calls.append(args)
        return resolver(*args)

    def sql_trace(connection, cursor, statement, parameters, context, executemany):
        if "JOIN card ON" in statement or "JOIN project_wiki ON" in statement:
            queries.append((statement, parameters))

    monkeypatch.setattr(cloud.service.card_attachment, "resolve_file_owner", tracked)
    event.listen(cloud.engine, "before_cursor_execute", sql_trace)
    try:
        page = read(cloud, view="media")
    finally:
        event.remove(cloud.engine, "before_cursor_execute", sql_trace)
    assert page["items"] == [] and page["has_more"] and page["next_cursor"]
    assert len(calls) == 500
    assert len(queries) == 6
    for statement, params in queries:
        assert "LIMIT" in statement and "deleted_at IS NULL" in statement
        assert (params["param_1"] if isinstance(params, dict) else params[-2]) <= 200
        if "JOIN card ON" in statement:
            assert "visibility" in statement and "owner_user_id" in statement
        else:
            assert "project_wiki_assigned_user" in statement and "project_assigned_user" in statement
    following = read(cloud, view="media", cursor=page["next_cursor"])
    assert [item["name"] for item in following["items"]] == ["photo.png"]
    assert not following["has_more"]


def test_recent_allowance_across_pages_is_20(cloud):
    add_files(cloud, 35)
    first = read(cloud, view="recent", limit=13)
    second = read(cloud, view="recent", limit=13, cursor=first["next_cursor"])
    assert len(first["items"]) == 13 and len(second["items"]) == 7
    assert not second["has_more"] and second["next_cursor"] is None


@pytest.mark.parametrize("change", ["tamper", "expiry", "view", "channel", "project", "actor"])
def test_scoped_authenticated_cursor_rejects_replay(cloud, monkeypatch, change):
    cursor = read(cloud, limit=1)["next_cursor"]
    if change == "tamper":
        cursor = cursor[:10] + ("A" if cursor[10] != "A" else "B") + cursor[11:]
    elif change == "expiry":
        from langboard.apps import BoardCloudLinks

        monkeypatch.setattr(BoardCloudLinks, "time", lambda: 9999999999)
    kwargs = dict(channel=CollaborationChannel.HumanUI, cursor=cursor)
    actor, project = cloud.actor, cloud.project
    if change == "view":
        kwargs["view"] = "files"
    elif change == "channel":
        kwargs["channel"] = CollaborationChannel.Api
    elif change == "project":
        with DbSession.use(readonly=False) as db:
            project = Project(owner_id=actor.id, title="Other accessible board")
            db.insert(project)
            db.insert(ProjectRole(project_id=project.id, user_id=actor.id, actions=["read"]))
    elif change == "actor":
        actor = cloud.other
        with DbSession.use(readonly=False) as db:
            db.insert(ProjectRole(project_id=project.id, user_id=actor.id, actions=["read"]))
    with pytest.raises(ApiException.BadRequest_400):
        read_board_cloud(cloud.service, actor, project.get_uid(), **kwargs)


def test_current_grants_and_source_revocation_apply_between_pages(cloud):
    first = read(cloud, limit=1)
    with cloud.engine.begin() as db:
        db.execute(delete(ProjectWikiAssignedUser))
        db.execute(update(Card).values(owner_user_id=cloud.other.id, created_by_user_id=cloud.other.id))
    second = read(cloud, cursor=first["next_cursor"])
    assert second["items"] == []
    with cloud.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=[]))
    with pytest.raises(ApiException.NotFound_404):
        read(cloud, cursor=first["next_cursor"])


def test_card_title_is_current_and_foreign_wiki_drift_is_denied(cloud, monkeypatch):
    resolver = cloud.service.card.resolve_readable_card

    def renamed(*args, **kwargs):
        with cloud.engine.begin() as db:
            db.execute(update(Card).values(title="Current card title"))
        return resolver(*args, **kwargs)

    monkeypatch.setattr(cloud.service.card, "resolve_readable_card", renamed)
    assert all(
        item["source"]["title"] == "Current card title" for item in read(cloud)["items"] if "card_uid" in item["source"]
    )
    wiki_resolver = cloud.service.project_wiki.resolve_readable_file

    with DbSession.use(readonly=False) as db:
        foreign = Project(owner_id=cloud.other.id, title="Foreign")
        db.insert(foreign)

    def moved(*args, **kwargs):
        attachment = wiki_resolver(*args, **kwargs)
        with cloud.engine.begin() as db:
            db.execute(update(ProjectWiki).values(project_id=foreign.id))
        return attachment

    monkeypatch.setattr(cloud.service.project_wiki, "resolve_readable_file", moved)
    assert all("wiki_uid" not in item["source"] for item in read(cloud)["items"])


@pytest.mark.parametrize("revoked", ["role", "wiki", "card_file", "project_file"])
def test_final_response_rechecks_previously_projected_sources(cloud, monkeypatch, revoked):
    import langboard.apps.BoardCloud as app

    native_file = None
    if revoked == "project_file":
        with DbSession.atomic() as db:
            native_file = ProjectAttachment(
                project_id=cloud.project.id, user_id=cloud.actor.id, filename="native.pdf", content_sha256="a" * 64,
                file=cloud.card_file.file.model_copy(update={"storage_name": "project_attachment", "filename": "native-project.pdf"}),
            )
            db.insert(native_file)
    original = app._item
    projected = set()
    revocation_applied = False

    def revoke_after_projection(attachment, project, source, kind):
        nonlocal revocation_applied
        item = original(attachment, project, source, kind)
        projected.add(item["id"])
        if not revocation_applied and len(projected) == (5 if native_file else 4):
            revocation_applied = True
            with cloud.engine.begin() as db:
                if revoked == "role":
                    db.execute(update(ProjectRole).values(actions=[]))
                elif revoked == "wiki":
                    db.execute(delete(ProjectWikiAssignedUser))
                elif revoked == "project_file":
                    db.execute(update(ProjectAttachment).where(ProjectAttachment.id == native_file.id)
                               .values(deleted_at=SafeDateTime.now()))
                else:
                    db.execute(update(CardAttachment).where(CardAttachment.id == cloud.card_file.id)
                               .values(deleted_at=SafeDateTime.now()))
        return item

    monkeypatch.setattr(app, "_item", revoke_after_projection)
    if revoked == "role":
        with pytest.raises(ApiException.NotFound_404):
            read(cloud)
    else:
        items = read(cloud)["items"]
        hidden = f"wiki_attachment:{cloud.wiki_file.get_uid()}" if revoked == "wiki" else f"card_attachment:{cloud.card_file.get_uid()}"
        if native_file:
            hidden = f"project_attachment:{native_file.get_uid()}"
        assert len(items) == (4 if native_file else 3)
        assert all(item["id"] != hidden for item in items)
    assert len(projected) == (5 if native_file else 4)
    assert revocation_applied
