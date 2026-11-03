"""Card/board deletion commits recorded vector cleanup with the source change."""

from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import Card, CardMetadata, Project, ProjectWiki
from langboard_shared.domain.services import DomainService
from sqlalchemy import select
from test_document_cleanup_outbox import cleanup_store, document_source, outbox, pending, pointer  # noqa: F401
from test_document_mcp_read import binding_store  # noqa: F401


@pytest.mark.parametrize("scope", ["card", "project"])
def test_scope_deletion_commits_cleanup_and_rolls_back_as_one_unit(document_source, cleanup_store, binding_store, monkeypatch, scope):  # noqa: F811
    metadata, card, _, document = document_source
    if scope == "project":
        ProjectWiki.__table__.create(binding_store)
    service = DomainService()
    card_module = import_module("langboard_shared.domain.services.factory.CardService")
    project_module = import_module("langboard_shared.domain.services.factory.ProjectService")
    with DbSession.atomic() as db:
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
        sibling = Card(project_id=project.id, project_column_id=card.project_column_id, title="Sibling")
        db.insert(sibling)
        siblings = [sibling]
        if scope == "project":
            for index in range(99):
                additional = Card(
                    project_id=project.id, project_column_id=card.project_column_id, title=f"Sibling {index}"
                )
                db.insert(additional)
                siblings.append(additional)
        other_project = Project(owner_id=project.owner_id, title="Untouched board")
        db.insert(other_project)
        unrelated = Card(project_id=other_project.id, project_column_id=card.project_column_id, title="Other board")
        db.insert(unrelated)
    sources = [(source, f"sibling-{index}") for index, source in enumerate(siblings)] + [(unrelated, "unrelated")]
    for source, generation in sources:
        metadata.save_documents(
            CardMetadata,
            source,
            [
                {
                    **document,
                    "embedding": {
                        "status": "indexed",
                        "pointer": {**pointer(), "generation": generation, "chunk_ids": [generation]},
                    },
                }
            ],
        )

    # Keep unrelated activity, scheduling and readiness subsystems outside this
    # transaction test; actual repositories, metadata and outbox writes run below.
    for module in (card_module, project_module):
        monkeypatch.setattr(module.InfraHelper, "get_by_id_like", lambda _model, value: value)
    monkeypatch.setattr(service.card.repo.checkitem, "get_all_started_checkitem_by_card", Mock(return_value=[]))
    monkeypatch.setattr(service.card.repo.checkitem, "get_all_started_checkitem_by_project", Mock(return_value=[]))
    monkeypatch.setattr(service.card, "_dependency_children", Mock(return_value=[]))
    monkeypatch.setattr(service.card, "publish_work_states", Mock())
    monkeypatch.setattr(service.card.repo.card_assigned_user, "delete_all_by_card", Mock())
    monkeypatch.setattr(service.card.repo.card_relationship, "delete_all_by_card", Mock())
    monkeypatch.setattr(service.card.repo.card, "reoder_after_delete", Mock())
    monkeypatch.setattr(card_module.BotScopeHelper, "delete_by_scope", Mock())
    monkeypatch.setattr(card_module.BotScheduleHelper, "unschedule_by_scope", Mock())
    monkeypatch.setattr(service.graph_approval_request, "cancel_pending_by_scope", Mock())
    for publisher, activity, bot in (
        (card_module.CardPublisher, card_module.CardActivityTask, card_module.CardBotTask),
        (project_module.ProjectPublisher, project_module.ProjectActivityTask, project_module.ProjectBotTask),
    ):
        monkeypatch.setattr(publisher, "deleted", Mock())
        kind = "card_deleted" if activity is card_module.CardActivityTask else "project_deleted"
        monkeypatch.setattr(activity, kind, Mock())
        monkeypatch.setattr(bot, kind, Mock())

    @contextmanager
    def execution():
        with DbSession.atomic():
            yield SimpleNamespace(watch_card_and_dependents=Mock())

    monkeypatch.setattr(card_module, "execution_readiness_uow", execution)

    def remove():
        if scope == "card":
            assert service.card._delete_card(object(), project, card)
        else:
            assert service.project.delete(object(), project)

    def deleted_at():
        model, identifier = (Card, card.id) if scope == "card" else (Project, project.id)
        with DbSession.use(readonly=False) as db:
            return db.exec(select(model.deleted_at).where(model.id == identifier)).first()[0]

    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with DbSession.atomic():
                remove()
                assert len(pending()) == (1 if scope == "card" else 101)
                cleanup_store.assert_not_called()
                raise RuntimeError("rollback")
        assert deleted_at() is None and pending() == []
        remove()
        assert deleted_at() is not None
        assert {event.pointer["generation"] for event in pending()} == (
            {"old"} if scope == "card" else {"old", *(f"sibling-{index}" for index in range(100))}
        )
        assert (
            metadata.publish_document_embedding(
                card,
                document["attachment_uid"],
                "current",
                "hash",
                {"status": "indexed", "pointer": {**pointer(), "generation": "late"}},
                expected_embedding=document["embedding"],
            )
            is False
        )
        before = len(pending())
        metadata.record_scope_embedding_cleanup(project, card if scope == "card" else None)
        assert len(pending()) == before
        assert cleanup_store.call_count == (1 if scope == "card" else 0)
        if scope == "project":
            cleanup_store.side_effect = None
            assert outbox.recover_cleanup(limit=100) == 100
            assert cleanup_store.call_count == 100 and len(pending()) == 101
        with DbSession.use(readonly=False) as db:
            assert (
                db.exec(SqlBuilder.select.column(Project.deleted_at).where(Project.id == other_project.id)).first()
                is None
            )
        assert (
            metadata.get_document_by_attachment_uid(CardMetadata, unrelated, document["attachment_uid"])["embedding"][
                "pointer"
            ]["generation"]
            == "unrelated"
        )
    finally:
        service.close()


@pytest.mark.parametrize("scope", ["card", "project"])
def test_scope_delete_fences_concurrent_publication(document_source, cleanup_store, binding_store, scope):  # noqa: F811
    if binding_store.dialect.name != "postgresql":
        pytest.skip("Row-lock contention requires PostgreSQL")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep
    from sqlalchemy import func, select

    metadata, card, _, document = document_source
    with DbSession.use(readonly=False) as db:
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
    started = Event()
    worker = {}

    def publish():
        with DbSession.atomic() as db:
            worker["pid"] = db.exec(select(func.pg_backend_pid())).first()[0]
            started.set()
            return metadata.publish_document_embedding(
                card,
                document["attachment_uid"],
                "current",
                "hash",
                {"status": "indexed", "pointer": {**pointer(), "generation": "late"}},
                expected_embedding=document["embedding"],
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with DbSession.atomic() as db:
            metadata.record_scope_embedding_cleanup(project, card if scope == "card" else None)
            owner = db.exec(select(func.pg_backend_pid())).first()[0]
            future = pool.submit(publish)
            assert started.wait(3), "Publication connection did not start"
            blocked = False
            deadline = monotonic() + 5
            while monotonic() < deadline:
                blockers = db.exec(select(func.pg_blocking_pids(worker["pid"]))).first()[0]
                if owner in blockers:
                    blocked = True
                    break
                sleep(0.02)
            assert blocked, "Publication did not wait for the source deletion transaction"
            assert not future.done()
            db.delete(card if scope == "card" else project)
        assert future.result(timeout=5) is False
    assert len(pending()) == 1 and pending()[0].pointer["generation"] == "old"
