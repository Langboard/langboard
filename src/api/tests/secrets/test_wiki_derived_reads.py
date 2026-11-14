"""Wiki realtime snapshots must not retain editor bodies in derived queues."""

import json
import pytest
from langboard_shared.core.broadcast import DispatcherQueue
from langboard_shared.core.db import EditorContentModel
from langboard_shared.domain.models import Project, ProjectWiki
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.publishers import ProjectWikiPublisher


@pytest.mark.parametrize("is_public", [True, False])
def test_wiki_update_dispatcher_does_not_retain_editor_body(monkeypatch, is_public):
    queued = []
    monkeypatch.setattr(DispatcherQueue, "put", lambda message: queued.append(message.model_dump(mode="json")))
    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(
        id=11,
        project_id=10,
        title="Wiki",
        order=0,
        is_public=is_public,
        content=EditorContentModel(content="fixture-sensitive-value"),
    )
    ProjectWikiPublisher.updated(project, wiki, {"content": {"content": wiki.content.content}, "editor_generation": 0})
    wiki.content = EditorContentModel(content="[masked]")
    wiki.editor_generation = 1
    ProjectWikiPublisher.updated(project, wiki, {"content": {"content": "[masked]"}, "editor_generation": 1})
    # Both original and replacement messages are durable; sanitizing only the latest is insufficient.
    assert len(queued) == 2
    assert "fixture-sensitive-value" not in json.dumps(queued)
    assert "[masked]" not in json.dumps(queued)
    assert all("content" not in message["data"]["data"] for message in queued)
    assert [message["data"]["data"]["editor_generation"] for message in queued] == [0, 1]
    assert queued[0]["data"]["publish_models"]["event"] == f"board:wiki:details:changed:{wiki.get_uid()}"


def test_wiki_created_queue_has_metadata_without_editor_snapshot(monkeypatch):
    queued = []
    monkeypatch.setattr(DispatcherQueue, "put", lambda message: queued.append(message.model_dump(mode="json")))
    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(
        id=11, project_id=10, title="Wiki", order=0, content=EditorContentModel(content="fixture-sensitive-value")
    )
    ProjectWikiPublisher.created(project, wiki)
    body = queued[0]["data"]["data"]["wiki"]
    assert "fixture-sensitive-value" not in json.dumps(queued)
    assert "content" not in body and "editor_generation" not in body
    assert body["uid"] == wiki.get_uid() and body["title"] == "Wiki"
    assert body["assigned_members"] == []


def test_noncontent_update_metadata_and_order_are_preserved(monkeypatch):
    queued = []
    monkeypatch.setattr(DispatcherQueue, "put", lambda message: queued.append(message.model_dump(mode="json")))
    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(id=11, project_id=10, title="Wiki", order=0)
    for title in ["First", "Second"]:
        ProjectWikiPublisher.updated(project, wiki, {"title": title})
    assert [message["data"]["data"] for message in queued] == [{"title": "First"}, {"title": "Second"}]
    assert all(message["data"]["publish_models"]["data_keys"] == ["title"] for message in queued)


def test_content_only_update_retains_native_generation_invalidation(monkeypatch):
    queued = []
    monkeypatch.setattr(DispatcherQueue, "put", lambda message: queued.append(message.model_dump(mode="json")))
    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(id=11, project_id=10, title="Wiki", order=0, editor_generation=4)
    ProjectWikiPublisher.updated(project, wiki, {"content": {"content": "fixture-sensitive-value"}})
    assert queued[0]["data"]["data"] == {"editor_generation": 4}


@pytest.fixture
def divergent_wiki_engines(monkeypatch):
    from langboard_shared.core.db.DbEngine import DbEngine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    primary, replica = [create_engine("sqlite://", poolclass=StaticPool) for _ in range(2)]
    for engine, content, generation in [(primary, "[masked]", 1), (replica, "fixture-sensitive-value", 0)]:
        ProjectWiki.__table__.create(engine)
        with Session(engine) as db:
            db.add(
                ProjectWiki(
                    id=11,
                    project_id=10,
                    title="Wiki",
                    order=0,
                    content=EditorContentModel(content=content),
                    editor_generation=generation,
                )
            )
            db.commit()
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: primary)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica)
    yield primary, replica
    primary.dispose()
    replica.dispose()


@pytest.mark.parametrize("selector", ["single", "single-model", "all", "by-uids", "service", "service-model"])
def test_body_reads_use_current_primary_after_secretization(divergent_wiki_engines, selector):
    from types import SimpleNamespace
    from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService
    from langboard_shared.infrastructure.repositories.factory.ProjectWikiRepository import ProjectWikiRepository

    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(id=11, project_id=10, title="Wiki", order=0)
    repository = ProjectWikiRepository(lambda _: None, lambda _: None)
    if selector in {"single", "single-model"}:
        rows = [repository.get_by_id_like(wiki if selector == "single-model" else wiki.get_uid())]
    elif selector == "all":
        rows = repository.get_all_by_project(project)
    elif selector == "by-uids":
        rows = repository.get_by_project_and_uids(project, {wiki.get_uid()})
    else:
        service = ProjectWikiService(lambda _: None, lambda _: None, SimpleNamespace(project_wiki=repository))
        rows = [service.get_by_id_like(wiki if selector == "service-model" else wiki.get_uid())]
    assert len(rows) == 1 and rows[0].editor_generation == 1
    assert rows[0].content.content == "[masked]"


def test_native_body_search_never_returns_replica_plaintext(divergent_wiki_engines):
    from types import SimpleNamespace
    from langboard.wiki_workspace.infrastructure import NativeWikiRepository

    project = SimpleNamespace(id=10, owner_id=1)
    service = SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda _: project))
    repository = NativeWikiRepository(SimpleNamespace(id=1, is_admin=True), service)
    assert repository.list_wikis("board", "fixture-sensitive-value", None, 10)["items"] == []
    result = repository.list_wikis("board", "[masked]", None, 10)
    assert len(result["items"]) == 1
    assert result["items"][0]["snippet"] == "[masked]"


def test_metadata_only_reads_keep_readonly_projection(divergent_wiki_engines):
    from types import SimpleNamespace
    from langboard.wiki_workspace.infrastructure import NativeWikiRepository
    from langboard_shared.infrastructure.repositories.factory.ProjectWikiRepository import ProjectWikiRepository
    from sqlalchemy import event

    primary, replica = divergent_wiki_engines
    queries = []
    event.listen(primary, "before_cursor_execute", lambda *args: queries.append(("primary", args[2])))
    event.listen(replica, "before_cursor_execute", lambda *args: queries.append(("replica", args[2])))
    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(id=11, project_id=10, title="Wiki", order=0)
    repository = ProjectWikiRepository(lambda _: None, lambda _: None)
    assert len(repository.get_headers_by_project_and_uids(project, {wiki.get_uid()})) == 1
    native = NativeWikiRepository(
        SimpleNamespace(id=1, is_admin=True), SimpleNamespace(project=SimpleNamespace(get_by_id_like=lambda _: project))
    )
    assert len(native.list_wikis("board", "", None, 10)["items"]) == 1
    assert len(queries) == 2 and all(engine == "replica" for engine, _ in queries)
    assert all("project_wiki.content" not in sql for _, sql in queries)


@pytest.mark.parametrize("side", ["before", "after"])
def test_revision_pages_use_primary_redaction_with_lagging_replica(divergent_wiki_engines, side):
    from types import SimpleNamespace
    from langboard.wiki_workspace.infrastructure import NativeWikiRepository
    from langboard_shared.domain.models import ProjectWikiActivity
    from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType
    from langboard_shared.infrastructure.repositories.factory.ProjectWikiRepository import ProjectWikiRepository
    from sqlalchemy.orm import Session

    primary, replica = divergent_wiki_engines
    for engine, body in ((primary, "[masked]"), (replica, "fixture-sensitive-value")):
        ProjectWikiActivity.__table__.create(engine)
        with Session(engine) as db:
            db.add(
                ProjectWikiActivity(
                    id=12,
                    project_id=10,
                    project_wiki_id=11,
                    activity_type=ProjectWikiActivityType.WikiUpdated,
                    activity_history={"changes": {key: {"content": {"content": body}} for key in ("before", "after")}},
                )
            )
            db.commit()
    wiki_repository = ProjectWikiRepository(lambda _: None, lambda _: None)
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _: Project(id=10, owner_id=1, title="Board")),
        project_wiki=SimpleNamespace(
            get_by_id_like=wiki_repository.get_by_id_like,
            convert_to_api_response=lambda *args: {"forbidden": False},
        ),
    )
    repository = NativeWikiRepository(SimpleNamespace(id=1, is_admin=True), service)
    wiki_uid = ProjectWiki(id=11, project_id=10, title="Wiki", order=0).get_uid()
    revision_uid = ProjectWikiActivity(id=12, activity_type=ProjectWikiActivityType.WikiUpdated).get_uid()
    # Current body already comes from primary. Historical snapshots need the same fence.
    assert repository.snapshot("board", wiki_uid).content == "[masked]"
    page = repository.revision_page("board", wiki_uid, revision_uid, side, None, 100)
    assert page["content"] == "[masked]"
    assert "fixture-sensitive-value" not in json.dumps(page)


@pytest.fixture
def divergent_wiki_activities(divergent_wiki_engines):
    from datetime import timedelta
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import (
        Card,
        ProjectActivity,
        ProjectWikiActivity,
        ProjectWikiAssignedUser,
        User,
        UserActivity,
    )
    from langboard_shared.domain.models.ProjectWikiActivity import ProjectWikiActivityType
    from sqlalchemy.orm import Session

    now = SafeDateTime.now()
    project = Project(id=10, owner_id=1, title="Board")
    wiki = ProjectWiki(id=11, project_id=10, title="Wiki", order=0)
    actor = User(id=1, firstname="Fixture", lastname="Actor", email="actor@example.invalid", password="test-only")
    for engine, body in zip(divergent_wiki_engines, ("[masked]", "fixture-sensitive-value"), strict=True):
        for model in (Project, Card, ProjectActivity, ProjectWikiActivity, ProjectWikiAssignedUser, UserActivity):
            model.__table__.create(engine)
        with Session(engine) as db:
            db.add(Project(id=project.id, owner_id=1, title=project.title))
            for offset, seconds in enumerate((-2, -1, 1)):
                created_at = now + timedelta(seconds=seconds)
                db.add(
                    ProjectWikiActivity(
                        id=12 + offset,
                        project_id=project.id,
                        project_wiki_id=wiki.id,
                        user_id=actor.id,
                        created_at=created_at,
                        activity_type=ProjectWikiActivityType.WikiUpdated,
                        activity_history={
                            "changes": {
                                side: {
                                    "content": {"content": body},
                                    "title": "Previous" if side == "before" else "Current",
                                }
                                for side in ("before", "after")
                            }
                        },
                    )
                )
                # Native assignee activities contain a reference, not a copied history body.
                db.add(
                    UserActivity(
                        id=112 + offset,
                        user_id=actor.id,
                        created_at=created_at,
                        refer_activity_table=ProjectWikiActivity.__tablename__,
                        refer_activity_id=12 + offset,
                        activity_history={},
                    )
                )
            db.add(
                UserActivity(
                    id=200,
                    user_id=2,
                    created_at=now,
                    refer_activity_table=ProjectWikiActivity.__tablename__,
                    refer_activity_id=12,
                    activity_history={},
                )
            )
            db.commit()
    return project, wiki, actor, now


@pytest.mark.parametrize("selector", ["repository", "service", "assignee-service", "personal-service"])
def test_wiki_activity_history_uses_primary_redaction_with_lagging_replica(divergent_wiki_activities, selector):
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.schema import TimeBasedPagination
    from langboard_shared.infrastructure.repositories.factory.ActivityRepository import ActivityRepository

    project, wiki, actor, now = divergent_wiki_activities
    repository = ActivityRepository(lambda _: None, lambda _: None)
    pagination = TimeBasedPagination(refer_time=now, limit=1)
    assert not DbSession.has_active_transaction()
    if selector == "repository":
        rows, count = repository.get_list_by_wiki(project, wiki, pagination)
        history = rows[0].activity_history
    else:
        service = _wiki_activity_service(repository, actor, project)
        if selector == "personal-service":
            rows, count, _ = service.get_api_list_by_user(actor, pagination)
        else:
            rows, count, _, _ = service.get_api_list_by_wiki(
                project, wiki, pagination, assignee=actor if selector == "assignee-service" else None
            )
        history = (
            rows[0]["refer"]["activity_history"]
            if selector in {"assignee-service", "personal-service"}
            else rows[0]["activity_history"]
        )
    assert len(rows) == 1 and count == 1
    assert all(history["changes"][side]["content"]["content"] == "[masked]" for side in ("before", "after"))
    if selector in {"assignee-service", "personal-service"}:
        current_wiki = rows[0]["references"]["project_wiki"]
        assert all((current_wiki["content"]["content"] == "[masked]", current_wiki["editor_generation"] == 1))


def _wiki_activity_service(repository, actor, project):
    from types import SimpleNamespace
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
    from langboard_shared.domain.services.factory.ActivityService import ActivityService

    context = CardVisibilityContext(
        channel=CollaborationChannel.Api,
        active=True,
        project_member=True,
        actor_user_id=actor.id,
        internal_member=False,
    )
    card_service = SimpleNamespace(resolve_work_visibility_contexts=lambda *_: {project.id: context})
    return ActivityService(lambda _: card_service, lambda _: None, SimpleNamespace(activity=repository))


@pytest.mark.parametrize("assignee", [False, True])
def test_wiki_activity_pages_and_counts_preserve_time_order_and_actor_scope(divergent_wiki_activities, assignee):
    from langboard_shared.core.schema import TimeBasedPagination
    from langboard_shared.infrastructure.repositories.factory.ActivityRepository import ActivityRepository

    project, wiki, actor, now = divergent_wiki_activities
    repository = ActivityRepository(lambda _: None, lambda _: None)
    kwargs = {"assignee": actor} if assignee else {}
    seen = []
    for page in (1, 2, 3):
        rows, count = repository.get_list_by_wiki(
            project, wiki, TimeBasedPagination(refer_time=now, limit=1, page=page), **kwargs
        )
        assert count == 1
        if page == 3:
            assert rows == []
        else:
            assert len(rows) == 1
            seen.append(int(rows[0].id))
            if not assignee:
                history = rows[0].activity_history
                assert history["changes"]["before"]["title"] == "Previous"
                assert history["changes"]["after"]["title"] == "Current"
    assert seen == ([113, 112] if assignee else [13, 12])
    assert (
        repository.get_list_by_wiki(project, wiki, TimeBasedPagination(refer_time=now), only_count=True, **kwargs) == 1
    )


@pytest.mark.parametrize(
    "selector", ["count", "assignee-count", "service-count", "assignee-service-count", "assignee-references"]
)
def test_wiki_activity_metadata_reads_keep_readonly_projection(
    divergent_wiki_engines, divergent_wiki_activities, selector
):
    from langboard_shared.core.schema import TimeBasedPagination
    from langboard_shared.infrastructure.repositories.factory.ActivityRepository import ActivityRepository
    from sqlalchemy import event

    project, wiki, actor, now = divergent_wiki_activities
    queries = []
    for label, engine in zip(("primary", "replica"), divergent_wiki_engines, strict=True):
        event.listen(engine, "before_cursor_execute", lambda *args, source=label: queries.append((source, args[2])))
    repository = ActivityRepository(lambda _: None, lambda _: None)
    if selector == "assignee-references":
        rows, count = repository.get_list_by_wiki(project, wiki, TimeBasedPagination(refer_time=now), assignee=actor)
        assert len(rows) == 1 and rows[0].activity_history == {} and count == 1
    elif "service" in selector:
        from types import SimpleNamespace
        from langboard_shared.domain.services.factory.ActivityService import ActivityService

        service = ActivityService(lambda _: None, lambda _: None, SimpleNamespace(activity=repository))
        assert (
            service.get_api_list_by_wiki(
                project,
                wiki,
                TimeBasedPagination(refer_time=now),
                only_count=True,
                assignee=actor if selector == "assignee-service-count" else None,
            )
            == 1
        )
    else:
        assert (
            repository.get_list_by_wiki(
                project,
                wiki,
                TimeBasedPagination(refer_time=now),
                only_count=True,
                assignee=actor if selector == "assignee-count" else None,
            )
            == 1
        )
    assert queries and all(source == "replica" for source, _ in queries)
    assert all("project_wiki_activity.activity_history" not in sql for _, sql in queries)


def test_project_activity_payload_reads_keep_existing_replica(divergent_wiki_engines, divergent_wiki_activities):
    from langboard_shared.core.schema import TimeBasedPagination
    from langboard_shared.domain.models import ProjectActivity, UserActivity
    from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
    from langboard_shared.infrastructure.repositories.factory.ActivityRepository import ActivityRepository
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    project, _, actor, now = divergent_wiki_activities
    queries = []
    for label, engine in zip(("primary", "replica"), divergent_wiki_engines, strict=True):
        with Session(engine) as db:
            db.add(
                ProjectActivity(
                    id=300,
                    project_id=project.id,
                    activity_type=ProjectActivityType.ProjectUpdated,
                    created_at=now,
                    activity_history={"state": label},
                )
            )
            db.add(
                UserActivity(
                    id=400,
                    user_id=actor.id,
                    created_at=now,
                    refer_activity_table=ProjectActivity.__tablename__,
                    refer_activity_id=300,
                )
            )
            db.commit()
        event.listen(engine, "before_cursor_execute", lambda *args, source=label: queries.append((source, args[2])))
    repository = ActivityRepository(lambda _: None, lambda _: None)
    rows, count = repository.get_list_by_project(project, TimeBasedPagination(refer_time=now))
    assert len(rows) == 1 and rows[0].activity_history == {"state": "replica"} and count == 0
    assert queries and all(source == "replica" for source, _ in queries)
    queries.clear()
    rows, count, _ = _wiki_activity_service(repository, actor, project).get_api_list_by_user(
        actor, TimeBasedPagination(refer_time=now, limit=10)
    )
    assert len(rows) == 3 and count == 1
    project_rows = [row for row in rows if row["references"]["refer_type"] == "project"]
    wiki_rows = [row for row in rows if row["references"]["refer_type"] == "project_wiki"]
    assert len(project_rows) == 1 and project_rows[0]["refer"]["activity_history"] == {"state": "replica"}
    assert len(wiki_rows) == 2
    assert all(
        row["refer"]["activity_history"]["changes"]["after"]["content"]["content"] == "[masked]" for row in wiki_rows
    )
    project_reads = [source for source, sql in queries if "project_activity.activity_history" in sql]
    wiki_reads = [
        source
        for source, sql in queries
        if "project_wiki_activity.activity_history" in sql or "project_wiki.content" in sql
    ]
    assert project_reads and all(source == "replica" for source in project_reads)
    assert wiki_reads and all(source == "primary" for source in wiki_reads)


@pytest.mark.parametrize(
    "notification_type", [kind.value for kind in NotificationType if kind != NotificationType.MentionedInWiki]
)
def test_nonwiki_notification_dispatcher_preserves_complete_payload(monkeypatch, notification_type):
    from langboard_shared.domain.models import User
    from langboard_shared.publishers.UserPublisher import UserPublisher

    queued = []
    monkeypatch.setattr(DispatcherQueue, "put", lambda message: queued.append(message.model_dump(mode="json")))
    user = User(id=1, firstname="Test", lastname="User", email="user@example.invalid", password="test-only")
    notification = {
        "uid": "fixture-notification",
        "type": notification_type,
        "message_vars": {"line": "ordinary notification body"},
        "records": {"project": {"uid": "fixture-project"}},
        "notifier_user": {"uid": "fixture-user"},
        "read_at": None,
    }
    UserPublisher.notified(user, notification)
    assert queued[0]["data"]["data"] == {"notification": notification}
    assert queued[0]["data"]["publish_models"]["topic_id"] == user.get_uid()
    assert queued[0]["data"]["publish_models"]["data_keys"] == ["notification"]
