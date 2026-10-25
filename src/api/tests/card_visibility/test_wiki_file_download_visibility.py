"""Wiki bytes require committed provenance and current board and wiki grants."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, delete, update
from langboard.routes.file import FileApi
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import ApiException
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectRole, ProjectWiki, ProjectWikiAssignedUser, ProjectWikiAttachment, User
from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService


@pytest.fixture
def fixture(monkeypatch):
    engine = create_engine("sqlite://")
    for model in (User, Project, ProjectAssignedUser, ProjectRole, ProjectWiki, ProjectWikiAssignedUser, ProjectWikiAttachment):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("ACL must read primary"))
    actor = User(firstname="Member", lastname="Fixture", email="fixture@example.invalid", password="fixture", activated_at=SafeDateTime.now())
    with DbSession.use(readonly=False) as db:
        db.insert(actor)
        project = Project(owner_id=actor.id + 1, title="Fixture")
        db.insert(project)
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
    yield SimpleNamespace(**locals())
    engine.dispose()


def mutate(f, denial):
    with f.engine.begin() as db:
        if denial == "anonymous":
            f.monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: 401)
        elif denial == "unassigned":
            db.execute(delete(ProjectWikiAssignedUser))
        elif denial == "membership":
            db.execute(delete(ProjectAssignedUser))
        elif denial == "role":
            db.execute(update(ProjectRole).values(actions=[]))
        elif denial == "orphan":
            db.execute(delete(ProjectWikiAttachment))
        elif denial == "foreign":
            db.execute(update(ProjectWiki).values(project_id=f.project.id + 100))
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
