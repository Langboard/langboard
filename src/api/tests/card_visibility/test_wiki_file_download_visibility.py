"""Wiki bytes require committed provenance and current board and wiki grants."""
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard.routes.file import FileApi
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import ApiException
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Project,
    ProjectAssignedUser,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    User,
)
from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService
from sqlalchemy import create_engine, delete, select, text, update


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def fixture(monkeypatch, request):
    database_url = request.param
    if database_url == "postgresql-test":
        database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not database_url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(database_url)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = "wiki_file_test_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
        with engine.begin() as db:
            db.execute(text('CREATE TABLE organization (id BIGINT PRIMARY KEY)'))
    for model in (User, Project, ProjectAssignedUser, ProjectRole, ProjectWiki, ProjectWikiAssignedUser, ProjectWikiAttachment):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("ACL must read primary"))
    actor = User(firstname="Member", lastname="Fixture", email="fixture@example.invalid", password="fixture", activated_at=SafeDateTime.now())
    with DbSession.use(readonly=False) as db:
        db.insert(actor)
        owner = User(firstname="Owner", lastname="Fixture", email="owner@example.invalid", password="fixture", activated_at=SafeDateTime.now())
        db.insert(owner)
        project = Project(owner_id=owner.id, title="Fixture")
        db.insert(project)
        foreign_project = Project(owner_id=owner.id, title="Foreign")
        db.insert(foreign_project)
        member = ProjectAssignedUser(project_id=project.id, user_id=actor.id)
        db.insert(member)
        role = ProjectRole(project_id=project.id, user_id=actor.id, actions=["read"])
        db.insert(role)
        wiki = ProjectWiki(project_id=project.id, title="Private", is_public=False)
        db.insert(wiki)
        assignment = ProjectWikiAssignedUser(project_assigned_id=member.id, project_wiki_id=wiki.id, user_id=actor.id)
        db.insert(assignment)
        attachment = ProjectWikiAttachment(user_id=actor.id, project_wiki_id=wiki.id, filename="proof.pdf", file=FileModel(storage_type="local", storage_name="wiki", filename="proof.pdf", original_filename="proof.pdf", path="/fixture"))
        db.insert(attachment)
    service = SimpleNamespace(project_wiki=ProjectWikiService(lambda _: None, lambda _: None, SimpleNamespace()))
    monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: actor)
    monkeypatch.setattr(FileApi.BaseStorage, "decrypt_storage_type", lambda _: "local")
    def download(*args):
        args[-1].write(b"wiki secret")
        return True
    reader = Mock(side_effect=download)
    monkeypatch.setattr(FileApi.Storage, "download_file", reader)
    try:
        yield SimpleNamespace(**locals())
    finally:
        if schema:
            with engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def mutate(f, denial):
    with f.engine.begin() as db:
        if denial == "anonymous":
            f.monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: 401)
        elif denial == "unassigned":
            db.execute(delete(ProjectWikiAssignedUser))
        elif denial == "membership":
            db.execute(delete(ProjectWikiAssignedUser))
            db.execute(delete(ProjectAssignedUser))
        elif denial == "role":
            db.execute(update(ProjectRole).values(actions=[]))
        elif denial == "orphan":
            db.execute(delete(ProjectWikiAttachment))
        elif denial == "foreign":
            db.execute(update(ProjectWiki).values(project_id=f.foreign_project.id))
        elif denial == "private":
            db.execute(delete(ProjectWikiAssignedUser))
            db.execute(update(ProjectWiki).values(is_public=False))
        elif denial == "replaced":
            db.execute(update(ProjectWikiAttachment).values(id=f.attachment.id + 100))
        elif denial == "file_replaced":
            file = f.attachment.file.model_copy(update={"path": "/replacement"})
            db.execute(update(ProjectWikiAttachment).values(file=file))
        elif denial == "provenance":
            file = f.attachment.file.model_copy(update={"storage_type": "s3"})
            db.execute(update(ProjectWikiAttachment).values(file=file))
        elif denial == "ambiguous":
            values = {key: getattr(f.attachment, key) for key in ("user_id", "project_wiki_id", "filename", "file", "order")}
            db.execute(ProjectWikiAttachment.__table__.insert().values(id=f.attachment.id + 100, **values))
        else:
            model = {"deleted": ProjectWiki, "attachment_deleted": ProjectWikiAttachment, "project_deleted": Project, "user_deleted": User}[denial]
            db.execute(update(model).values(deleted_at=SafeDateTime.now()))


def read(f):
    return FileApi.get_file(SimpleNamespace(scope={}), "encrypted", "wiki", "proof.pdf", f.service)


@pytest.mark.parametrize("denial", ["anonymous", "unassigned", "membership", "role", "orphan", "foreign", "deleted", "attachment_deleted", "project_deleted", "user_deleted", "provenance", "ambiguous"])
def test_hidden_wiki_bytes_rejected_before_storage(fixture, denial):
    mutate(fixture, denial)
    with pytest.raises(ApiException.NotFound_404):
        read(fixture)
    fixture.reader.assert_not_called()


@pytest.mark.parametrize("denial", ["unassigned", "membership", "role", "orphan", "foreign", "deleted", "attachment_deleted", "project_deleted", "user_deleted", "replaced", "file_replaced", "provenance", "ambiguous", "private"])
def test_wiki_grants_rechecked_after_spool(fixture, denial):
    if denial == "private":
        with fixture.engine.begin() as db:
            db.execute(update(ProjectWiki).values(is_public=True))
    def download(*args):
        args[-1].write(b"wiki secret")
        mutate(fixture, denial)
        return True
    fixture.reader.side_effect = download
    with pytest.raises(ApiException.NotFound_404):
        read(fixture)
    fixture.reader.assert_called_once()


@pytest.mark.parametrize("public", [False, True])
def test_authorized_wiki_bytes_and_no_store(fixture, public):
    if public:
        with fixture.engine.begin() as db:
            db.execute(delete(ProjectWikiAssignedUser))
            db.execute(update(ProjectWiki).values(is_public=True))
    response = read(fixture)
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Authorization, Cookie, X-Api-Key, X-Api-Token"
    file = response.background.func.__self__
    assert file.read() == b"wiki secret"
    file.close()


def test_public_wiki_still_requires_authenticated_board_member(fixture):
    with fixture.engine.begin() as db:
        db.execute(update(ProjectWiki).values(is_public=True))
    mutate(fixture, "anonymous")
    with pytest.raises(ApiException.NotFound_404):
        read(fixture)
    fixture.reader.assert_not_called()


@pytest.mark.parametrize("privilege", ["owner", "admin"])
def test_native_private_wiki_privileged_visibility(fixture, privilege):
    with fixture.engine.begin() as db:
        db.execute(delete(ProjectWikiAssignedUser))
        if privilege == "owner":
            db.execute(update(Project).values(owner_id=fixture.actor.id))
        else:
            db.execute(update(User).values(is_admin=True))
            db.execute(delete(ProjectRole))
    response = read(fixture)
    response.background.func.__self__.close()


def test_cached_admin_actor_cannot_survive_current_demotion(fixture):
    fixture.actor.is_admin = True
    mutate(fixture, "unassigned")
    with pytest.raises(ApiException.NotFound_404):
        read(fixture)
    fixture.reader.assert_not_called()


@pytest.mark.parametrize("boundary", ["resolver", "before_storage", "after_spool"])
@pytest.mark.parametrize("denial", ["role", "membership", "unassigned", "user_deleted"])
def test_retained_authority_cannot_survive_committed_revocation(fixture, boundary, denial):
    f = fixture
    with DbSession.atomic() as db:
        # Keep identities alive across the independent committed authority change.
        retained_role = db.exec(select(ProjectRole).where(ProjectRole.id == f.role.id)).first()[0]
        retained_member = db.exec(select(ProjectAssignedUser).where(ProjectAssignedUser.id == f.member.id)).first()[0]
        retained_actor = db.exec(select(User).where(User.id == f.actor.id)).first()[0]
        assert retained_role.actions == ["read"]
        assert retained_member.id == f.member.id and retained_actor.activated_at
        assert f.service.project_wiki.resolve_readable_file(f.actor, "local", "wiki", "proof.pdf") is not None

        if boundary == "after_spool":
            def download(*args):
                args[-1].write(b"wiki secret")
                mutate(f, denial)
                return True
            f.reader.side_effect = download
        else:
            mutate(f, denial)

        with f.engine.connect() as committed:
            if boundary != "after_spool" and denial == "role":
                assert committed.execute(select(ProjectRole.actions)).scalar_one() == []
                assert retained_role.actions == ["read"]
        if boundary == "resolver":
            assert f.service.project_wiki.resolve_readable_file(f.actor, "local", "wiki", "proof.pdf") is None
        else:
            with pytest.raises(ApiException.NotFound_404):
                response = read(f)
                response.background.func.__self__.close()
        if boundary == "after_spool":
            f.reader.assert_called_once()
        else:
            f.reader.assert_not_called()
