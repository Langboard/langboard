"""Wiki realtime snapshots must not retain editor bodies in derived queues."""

import json
import pytest
from langboard_shared.core.broadcast import DispatcherQueue
from langboard_shared.core.db import EditorContentModel
from langboard_shared.domain.models import Project, ProjectWiki
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
