"""Recent-card restoration must not disclose a vault or revoked membership."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Card,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    CardCommentCompaction,
    CardMetadata,
    CardRelationship,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    InternalBot,
    KeywordSearchConfiguration,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    UserCardReadState,
    WorkflowStageDefinition,
)
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import CardRelationshipRepository
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.ChecklistRepository import ChecklistRepository
from langboard_shared.infrastructure.repositories.factory.ProjectAssignedUserRepository import (
    ProjectAssignedUserRepository,
)
from langboard_shared.infrastructure.repositories.factory.ProjectColumnRepository import ProjectColumnRepository
from langboard_shared.infrastructure.repositories.factory.UserCardReadStateRepository import UserCardReadStateRepository
from langboard_shared.publishers import CardPublisher
from sqlalchemy import create_engine


def test_recent_cards_revalidate_actor_and_filter_visibility_before_return(monkeypatch):
    engine = create_engine("sqlite://")
    for model in (User, Project, ProjectColumn, ProjectAssignedUser, Card, CardComment, CardAttachment, CardMetadata, WorkflowStageDefinition, Checklist, Checkitem, CardRelationship, GlobalCardRelationshipType, ProjectRole, CardAssignedUser, UserCardReadState, KeywordSearchConfiguration, InternalBot, CardCommentCompaction):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        with DbSession.use(readonly=False) as db:
            users = [User(firstname="Fixture", lastname=str(i), email=f"u{i}@example.invalid",
                          password="test-only", activated_at=SafeDateTime.now()) for i in range(2)]
            for user in users:
                db.insert(user)
            owner, member = users
            project = Project(owner_id=owner.id, title="Scoped recent fixture")
            db.insert(project)
            column = ProjectColumn(project_id=project.id, name="Work")
            db.insert(column)
            db.insert(ProjectColumn(project_id=project.id, name="Archive", is_archive=True))
            assignment = ProjectAssignedUser(project_id=project.id, user_id=member.id)
            db.insert(assignment)
            db.insert(ProjectRole(project_id=project.id, user_id=member.id, actions=["read"]))
            cards = [Card(project_id=project.id, project_column_id=column.id, title=visibility,
                          visibility=visibility, owner_user_id=vault_owner,
                          created_by_user_id=vault_owner) for visibility, vault_owner in (
                              ("SHARED", None), ("INTERNAL", None),
                              ("PRIVATE", member.id), ("PRIVATE", owner.id))]
            for card in cards:
                db.insert(card)
                db.insert(CardAssignedUser(card_id=card.id, user_id=member.id))
                db.insert(Checklist(card_id=card.id, title="Scoped checklist"))
                db.insert(CardAttachment(card_id=card.id, user_id=owner.id,
                    filename="proof.pdf", file=FileModel(storage_type="test", storage_name="test", original_filename="proof.pdf", path="/tmp/fixture", filename="proof.pdf"),
                    document_text="SearchProof document"))
        with DbSession.use(readonly=False) as db:
            relation_type = GlobalCardRelationshipType(parent_name="Parent", child_name="Child")
            db.insert(relation_type)
            edges = []
            for left, right in ((0, 1), (0, 2), (0, 3), (2, 3)):
                edge = CardRelationship(card_id_parent=cards[left].id, card_id_child=cards[right].id, relationship_type_id=relation_type.id)
                db.insert(edge)
                edges.append(edge)
        events = Mock()
        monkeypatch.setattr(CardPublisher, "read_state_changed", events)
        repository = SimpleNamespace(
            card=CardRepository(lambda _: None, lambda _: None),
            user_card_read_state=UserCardReadStateRepository(None, None),
            project_assigned_user=ProjectAssignedUserRepository(lambda _: None, lambda _: None),
        )
        content_reader = SimpleNamespace(api_blocks_by_cards=lambda ids: {card_id: [{"uid": str(card_id)}] for card_id in ids})
        scim = SimpleNamespace(is_employee=lambda user: False)
        compaction_reader = SimpleNamespace(search_metadata_candidates=lambda *args, **kwargs: {})
        readers = {"CardContentBlockService": content_reader, "CardCommentCompactionService": compaction_reader}
        service = CardService(lambda cls: readers.get(cls.__name__, scim), lambda _: None, repository)
        uids = [card.get_uid() for card in cards]
        for channel in CollaborationChannel:
            expected = {uids[0], uids[2]} if channel in (CollaborationChannel.HumanUI, CollaborationChannel.Mcp) else {uids[0]}
            assert set(service.get_existing_uids(project, uids, user=member, channel=channel)) == expected
            assert {card.get_uid() for card in service.get_visible_by_project(project, member, channel)} == expected
            found = service.search_context_by_project(project, "SearchProof", user=member, channel=channel)
            expected_visibility = {card.get_uid(): card.visibility for card in cards}
            assert all(item["visibility"] == expected_visibility[item["uid"]] for item in found)
            assert {row["uid"] for row in found} == expected
            assert all(row["document_matches"][0]["snippet"] == "SearchProof document" for row in found)
            _, context = service.resolve_visibility_context(project, member, channel)
            # Scope both keyset rows and total before LIMIT, not after pagination.
            rows = repository.card.get_page_by_project(project, 1, context=context)
            assert len(rows) == min(2, len(expected))
            assert all(card.get_uid() in expected for card, _ in rows)
            assert repository.card.count_by_project(project, context=context) == len(expected)
            collected = []
            before_time = before_card = None
            while True:
                batch = repository.card.get_page_by_project(project, 1, before_time, before_card, context=context)
                if not batch:
                    break
                current = batch[0][0]
                collected.append(current.get_uid())
                before_time, before_card = current.updated_at, current.id
            assert set(collected) == expected and len(collected) == len(expected)
            service.get_work_states = lambda cards, **kwargs: {card.id: {"scoped": kwargs["context"] == context} for card in cards}
            service._get_linked_resource_payloads = lambda *args, **kwargs: {}
            api_cards, total, cursor = service.get_api_page_by_project(project, 1, user_or_bot=member, channel=channel, include_closed=True)
            assert total == len(expected) and api_cards[0]["uid"] in expected
            assert api_cards[0]["work_state"] == {"scoped": True}
            assert (cursor is not None) == (len(expected) > 1)
            assert service.get_api_page_by_project(project, 1, user_or_bot=None, channel=channel) is None
            work_contexts = service.resolve_work_visibility_contexts(member, channel)
            assert work_contexts[int(project.id)] == context
            now = SafeDateTime.now()
            work_rows = repository.card.get_my_work_page(member, [project], {"assigned"}, [], now, now, "updated_at", None, None, 1, contexts=work_contexts)
            assert len(work_rows) == 1 and work_rows[0][0].get_uid() in expected
            all_work = repository.card.get_my_work_page(member, [project], {"assigned"}, [], now, now, "updated_at", None, None, 25, contexts=work_contexts)
            assert {row[0].get_uid() for row in all_work} == expected
            dashboard = repository.card.get_dashboard_list_scroller(member, SimpleNamespace(refer_time=now, page=1, limit=25), contexts=work_contexts)
            assert {row[0].get_uid() for row in dashboard} == expected
            settings_cards = service.get_api_list_by_project(project, member, channel=channel)
            assert {row["uid"] for row in settings_cards} == expected
            assert all(row["content_blocks"][0]["uid"] in {str(card.id) for card in cards if card.get_uid() in expected} for row in settings_cards)
            assert service.get_api_list_by_project(project, None, channel=channel) == []


            for candidate in cards:
                resolved_card = service.resolve_readable_card(project, candidate, member, channel)
                events.reset_mock()
                seen_result = service.mark_card_seen(member, candidate, project, channel=channel)
                unread_result = service.set_card_read_state(member, project, candidate, False, channel=channel)
                receipt = service.get_card_read_state(project, candidate, user=member, channel=channel)
                allowed = candidate.get_uid() in expected
                assert (seen_result is not None) == allowed
                assert (unread_result is not None) == allowed
                assert (receipt is not None) == allowed
                assert events.call_count == (2 if allowed else 0)
                if allowed:
                    assert unread_result["seen_change_seq"] == -1
                    assert receipt["readers"] == []

                assert (resolved_card is not None) == (candidate.get_uid() in expected)
                if resolved_card is None:
                    assert service.get_details(project, candidate, member, channel=channel) is None
            assert service.resolve_readable_card(project.id + 1, cards[0], member, channel) is None
            visible_edges = CardRelationshipRepository(None, None).get_all_by_card(cards[0], context=context)
            assert [edge.id for edge, _ in visible_edges] == ([edges[0].id] if context.can_read_internal else [])
            assert CardRelationshipRepository(None, None).get_all_by_card(cards[2], context=context) == []
            cutoff = SafeDateTime.now()
            board_cards = repository.card.get_board_list(project, cutoff, context=context)
            assert {card.get_uid() for card, _ in board_cards} == expected
            visible_ids = {card.id for card, _ in board_cards}
            checklists = ChecklistRepository(None, None).get_all_by_project(project, context=context)
            assert {checklist.card_id for checklist in checklists} == visible_ids
            columns = ProjectColumnRepository(None, None)
            counts = columns.get_all_by_project(project, context=context)
            assert next(count for item, count in counts if item.id == column.id) == len(expected)
            assert columns.get_work_counts(project, context=context)[column.id] == {
                "open_count": len(expected), "incomplete_count": len(expected),
            }
            excerpts = repository.card.search_document_matches(project, [card.id for card in cards], "SearchProof", context=context)
            assert {cards[i].id for i, uid in enumerate(uids) if uid in expected} == set(excerpts)
            limited = repository.card.search_context_by_project(project, "SearchProof", limit=1, context=context)
            assert len(limited) == 1 and limited[0][0].get_uid() in expected
        # A role/assignment on a foreign board cannot stand in for membership.
        with DbSession.use(readonly=False) as db:
            foreign_project = Project(owner_id=owner.id, title="Not a member")
            db.insert(foreign_project)
            foreign_column = ProjectColumn(project_id=foreign_project.id, name="Foreign")
            db.insert(foreign_column)
            foreign_card = Card(project_id=foreign_project.id, project_column_id=foreign_column.id, title="Foreign hidden", visibility="SHARED")
            db.insert(foreign_card)
            db.insert(CardAssignedUser(card_id=foreign_card.id, user_id=member.id))
            db.insert(ProjectRole(project_id=foreign_project.id, user_id=member.id, actions=["*"]))
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.project_id == project.id)).first()
        contexts = service.resolve_work_visibility_contexts(member, CollaborationChannel.Mcp)
        assert set(contexts) == {int(project.id)}
        now = SafeDateTime.now()
        rows = repository.card.get_my_work_page(member, [project, foreign_project], {"assigned"}, [], now, now, "updated_at", None, None, 25, contexts=contexts)
        assert foreign_card.id not in {row[0].id for row in rows}
        with DbSession.use(readonly=False) as db:
            no_read = role.model_copy(deep=True)
            no_read.actions = ["update"]
            db.update(no_read)
        assert service.resolve_work_visibility_contexts(member, CollaborationChannel.Mcp) == {}
        assert repository.card.get_my_work_page(member, [project], {"assigned"}, [], now, now, "updated_at", None, None, 25, contexts={}) == []
        with DbSession.use(readonly=False) as db:
            no_read.actions = ["read"]
            db.update(no_read)
        # A cached SHARED object cannot bypass a committed visibility change.
        with DbSession.use(readonly=False) as db:
            changed = cards[0].model_copy(deep=True)
            changed.visibility = "INTERNAL"
            db.update(changed)
        assert cards[0].visibility == "SHARED"
        assert service.resolve_readable_card(project, cards[0], member, CollaborationChannel.Mcp) is None
        events.reset_mock()
        assert service.mark_card_seen(member, cards[0], project, channel=CollaborationChannel.Mcp) is None
        events.assert_not_called()
        assert service.get_api_page_by_project(project, 1, user_or_bot=member, channel=CollaborationChannel.Mcp)[1] == 1
        with DbSession.use(readonly=False) as db:
            changed.visibility = "SHARED"
            db.update(changed)
        assert service.resolve_readable_card(project, cards[0], member, CollaborationChannel.Mcp) is not None
        attachment_service = CardAttachmentService(lambda _: None, lambda _: None, repository)
        with DbSession.use(readonly=False) as db:
            attachment = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.card_id == cards[0].id)).first()
            deleted_attachment = attachment.model_copy(deep=True)
            deleted_attachment.deleted_at = SafeDateTime.now()
            db.update(deleted_attachment)
        assert attachment.deleted_at is None
        assert attachment_service.get_by_id_like(attachment, consistent=True) is None
        scim.is_employee = lambda user: True
        assert set(service.get_existing_uids(project, uids, user=member, channel=CollaborationChannel.Mcp)) == set(uids[:3])
        _, internal_context = service.resolve_visibility_context(project, member, CollaborationChannel.Mcp)
        assert [edge.id for edge, _ in CardRelationshipRepository(None, None).get_all_by_card(cards[0], limit=1, context=internal_context)] == [edges[0].id]
        scim.is_employee = lambda user: None
        assert service.get_existing_uids(project, uids, user=member) == [uids[0]]
        # The auth User remains active in memory after primary DB revocation.
        with DbSession.use(readonly=False) as db:
            inactive = member.model_copy(deep=True)
            inactive.activated_at = None
            db.update(inactive)
        assert member.activated_at is not None
        assert service.mark_card_seen(member, cards[0], project, channel=CollaborationChannel.Mcp) is None
        assert service.get_card_read_state(project, cards[0], user=member, channel=CollaborationChannel.Mcp) is None
        assert service.resolve_work_visibility_contexts(member, CollaborationChannel.Mcp) == {}
        assert service.get_api_list_by_project(project, member, channel=CollaborationChannel.Mcp) == []
        assert service.get_api_page_by_project(project, 1, user_or_bot=member, channel=CollaborationChannel.Mcp) is None
        assert service.resolve_readable_card(project, cards[0], member, CollaborationChannel.Mcp) is None
        assert service.get_existing_uids(project, uids, user=member, channel=CollaborationChannel.Mcp) == []
        assert service.search_context_by_project(project, "SearchProof", user=member, channel=CollaborationChannel.Mcp) == []
        with DbSession.use(readonly=False) as db:
            inactive.activated_at = member.activated_at
            db.update(inactive)
        assert service.get_existing_uids(project, uids, user=member, channel=CollaborationChannel.Mcp)
        with DbSession.use(readonly=False) as db:
            db.delete(assignment)
        assert service.get_existing_uids(project, uids, user=member, channel=CollaborationChannel.Mcp) == []
        assert service.get_api_page_by_project(project, 1, user_or_bot=member, channel=CollaborationChannel.Mcp) == ([], 0, None)
        assert service.get_api_list_by_project(project, member, channel=CollaborationChannel.Mcp) == []
        assert service.mark_card_seen(member, cards[0], project, channel=CollaborationChannel.Mcp) is None
        assert service.get_card_read_state(project, cards[0], user=member, channel=CollaborationChannel.Mcp) is None
        assert service.resolve_work_visibility_contexts(member, CollaborationChannel.Mcp) == {}
        assert set(service.get_existing_uids(project, uids, user=owner, channel=CollaborationChannel.HumanUI)) == {uids[0], uids[3]}
        with DbSession.use(readonly=False) as db:
            removed_project = project.model_copy(deep=True)
            removed_project.deleted_at = SafeDateTime.now()
            db.update(removed_project)
        assert project.deleted_at is None
        assert service.get_existing_uids(project, uids, user=owner, channel=CollaborationChannel.HumanUI) == []
        assert service.get_existing_uids(project, [], user=owner) == []
        with pytest.raises(ValueError, match="200"):
            service.get_existing_uids(project, [uids[0]] * 201, user=owner)
    finally:
        engine.dispose()


def _batch_repository(service, project):
    from sqlalchemy import text
    engine = DbEngine.get_main_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            db.execute(text("ALTER TABLE project_column ADD COLUMN deleted_at TIMESTAMPTZ, ADD COLUMN project_id BIGINT"))
            db.execute(text("UPDATE project_column SET project_id = :project_id WHERE id = 0"), {"project_id": int(project.id)})
    else:
        ProjectColumn.__table__.create(engine)
        with engine.begin() as db:
            column = ProjectColumn(id=0, project_id=project.id, name="Fixture")
            db.execute(ProjectColumn.__table__.insert().values(**{c.name: getattr(column, c.name) for c in ProjectColumn.__table__.columns}))
    service.repo.card = CardRepository(None, None)


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
def test_batch_current_authority_exact_pairs_and_projection(current_card, monkeypatch):
    from sqlalchemy import event
    user, project, card, service = current_card
    _batch_repository(service, project)
    engine = __import__("langboard_shared.core.db.DbEngine", fromlist=["DbEngine"]).DbEngine.get_main_engine()
    with DbSession.use(readonly=False) as db:
        other = Project(owner_id=user.id, title="Second board")
        db.insert(other)
        column = ProjectColumn(id=SnowflakeID(), project_id=other.id, name="Second board column")
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text
            db.exec(text("INSERT INTO project_column (id, project_id) VALUES (:id, :project_id)").bindparams(id=int(column.id), project_id=int(other.id)))
        else:
            db.exec(ProjectColumn.__table__.insert().values(**{c.name: getattr(column, c.name) for c in ProjectColumn.__table__.columns}))
        second = Card(project_id=other.id, project_column_id=column.id, title="Second", visibility="SHARED")
        db.insert(second)
        card.visibility = "SHARED"
        card.owner_user_id = None
        db.update(card)
    pairs = [(project.get_uid(), card.get_uid()), (other.get_uid(), second.get_uid())]
    observed = []
    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            observed.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert set(service.get_existing_pairs(pairs + pairs, user=user)) == set(pairs)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert service.get_existing_pairs([("!!!!!!!!!!!", card.get_uid()), (project.get_uid(), "unknown")], user=user) == []
    # App ownership and linked wiki provenance preserve the same native read policy.
    from langboard_shared.domain.models import CardAppOwnership
    CardAppOwnership.__table__.create(engine)
    with DbSession.use(readonly=False) as db:
        db.insert(CardAppOwnership(card_id=second.id, app_key="fixture-app"))
        second.source_type = Card.LINKED_RESOURCE_PROJECT_WIKI
        second.source_uid = "fixture-wiki"
        db.update(second)
    assert set(service.get_existing_pairs(pairs, user=user)) == set(pairs)
    assert len(observed) == 3
    assert "project.id IN" in observed[1]
    projection = observed[-1].split("FROM")[0]
    assert "card.project_id" in projection and "card.id" in projection
    assert "title" not in projection and "description" not in projection and "last_change" not in projection
    assert "LIMIT" in observed[-1]
    assert service.get_existing_pairs([(project.get_uid(), second.get_uid()), (other.get_uid(), card.get_uid())], user=user) == []
    with DbSession.use(readonly=False) as db:
        second.project_column_id = 0
        db.update(second)
    assert service.get_existing_pairs(pairs, user=user) == [pairs[0]]
    with DbSession.use(readonly=False) as db:
        second.project_column_id = column.id
        db.update(second)
    with pytest.raises(ValueError, match="200"):
        service.get_existing_pairs(pairs * 101, user=user)
    with DbSession.use(readonly=False) as db:
        second.archived_at = SafeDateTime.now()
        db.update(second)
    assert service.get_existing_pairs(pairs, user=user) == [pairs[0]]
    from sqlalchemy import text
    with engine.begin() as db:
        db.execute(text("UPDATE project_column SET deleted_at = CURRENT_TIMESTAMP WHERE id = 0"))
    assert service.get_existing_pairs(pairs, user=user) == []
    with engine.begin() as db:
        db.execute(text("UPDATE project_column SET deleted_at = NULL WHERE id = 0"))
    with DbSession.use(readonly=False) as db:
        deleted = card.model_copy(deep=True)
        deleted.deleted_at = SafeDateTime.now()
        db.update(deleted)
    assert service.get_existing_pairs(pairs, user=user) == []


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
def test_batch_role_revocation_channels_and_account(current_card):
    owner, project, card, service = current_card
    _batch_repository(service, project)
    with DbSession.use(readonly=False) as db:
        member = User(firstname="Current", lastname="Member", email="member@example.invalid", password="test-only", activated_at=SafeDateTime.now())
        db.insert(member)
        assignment = ProjectAssignedUser(project_id=project.id, user_id=member.id)
        role = ProjectRole(project_id=project.id, user_id=member.id, actions=["read"])
        db.insert(assignment)
        db.insert(role)
        card.owner_user_id = member.id
        card.created_by_user_id = member.id
        db.update(card)
    pairs = [(project.get_uid(), card.get_uid())]
    for channel in CollaborationChannel:
        expected = pairs if channel in (CollaborationChannel.HumanUI, CollaborationChannel.Mcp) else []
        assert service.get_existing_pairs(pairs, user=member, channel=channel) == expected
    with DbSession.use(readonly=False) as db:
        card.visibility = "INTERNAL"
        card.owner_user_id = None
        db.update(card)
    assert service.get_existing_pairs(pairs, user=member, channel=CollaborationChannel.HumanUI) == []
    with DbSession.use(readonly=False) as db:
        card.visibility = "SHARED"
        db.update(card)
        role.actions = ["update"]
        db.update(role)
    assert service.get_existing_pairs(pairs, user=member) == []
    with DbSession.use(readonly=False) as db:
        role.actions = ["read"]
        db.update(role)
    assert service.get_existing_pairs(pairs, user=member) == pairs
    with DbSession.use(readonly=False) as db:
        db.delete(assignment)
    assert service.get_existing_pairs(pairs, user=member) == []
    assert service.get_existing_pairs(pairs, user=owner) == pairs
    with DbSession.use(readonly=False) as db:
        inactive = owner.model_copy(deep=True)
        inactive.activated_at = None
        db.update(inactive)
    assert service.get_existing_pairs(pairs, user=owner) == []


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
def test_batch_authenticated_native_http(current_card, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from jwt import encode
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.dashboard.DashboardApi import get_available_recent_card_pairs
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.Env import Env
    user, project, card, card_service = current_card
    _batch_repository(card_service, project)
    service = SimpleNamespace(card=card_service, close=lambda: None)
    monkeypatch.setattr(importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is get_available_recent_card_pairs:
            for dep in route.dependant.dependencies:
                if dep.name == "service":
                    app.dependency_overrides[dep.call] = lambda: service
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    token = encode({"sub": str(user.id), "internal": "bot", "api_permission_level": "read",
        "exp": int(SafeDateTime.now().timestamp()) + 300, "iss": Env.PROJECT_NAME}, Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)
    headers = {"X-Api-Token": token}
    payload = {"cards": [{"project_uid": project.get_uid(), "card_uid": card.get_uid()}]}
    with TestClient(app) as client:
        assert client.post("/dashboard/cards/available", json=payload).status_code == 401
        from langboard_shared.core.security import AuthSecurity
        access, refresh = AuthSecurity.authenticate(user.id)
        native_headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
        native = client.post("/dashboard/cards/available", json=payload, headers=native_headers)
        assert native.status_code == 200 and native.json() == payload
        # Internal API channel cannot disclose a human's private card.
        response = client.post("/dashboard/cards/available", json=payload, headers=headers)
        assert response.status_code == 200 and response.json() == {"cards": []}
        with DbSession.use(readonly=False) as db:
            card.visibility = "SHARED"
            card.owner_user_id = None
            db.update(card)
        response = client.post("/dashboard/cards/available", json=payload, headers=headers)
        assert response.status_code == 200 and response.json() == payload
        assert client.post("/dashboard/cards/available", json={"cards": payload["cards"] * 201}, headers=headers).status_code == 400
        with DbSession.use(readonly=False) as db:
            member = User(firstname="HTTP", lastname="Reader", email="http-reader@example.invalid", password="test-only", activated_at=SafeDateTime.now())
            db.insert(member)
            db.insert(ProjectAssignedUser(project_id=project.id, user_id=member.id))
            grant = ProjectRole(project_id=project.id, user_id=member.id, actions=["read"])
            db.insert(grant)
        member_access, member_refresh = AuthSecurity.authenticate(member.id)
        member_headers = {"Authorization": f"Bearer {member_access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={member_refresh}"}
        assert client.post("/dashboard/cards/available", json=payload, headers=member_headers).json() == payload
        with DbSession.use(readonly=False) as db:
            grant.actions = ["update"]
            db.update(grant)
        assert client.post("/dashboard/cards/available", json=payload, headers=member_headers).json() == {"cards": []}
        with DbSession.use(readonly=False) as db:
            inactive = user.model_copy(deep=True)
            inactive.activated_at = None
            db.update(inactive)
        response = client.post("/dashboard/cards/available", json=payload, headers=headers)
        # Account revocation is rejected by authentication before card projection.
        assert response.status_code == 401
