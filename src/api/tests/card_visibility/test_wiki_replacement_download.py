# ruff: noqa: F811 - pytest resolves imported native fixtures by parameter name.
"""Actual file route follows retained wiki locators only under current source ACL."""

from types import SimpleNamespace
import pytest
from langboard.routes.file import FileApi
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage
from langboard_shared.domain.models import ProjectWikiAssignedUser
from sqlalchemy import delete
from src.api.tests.card_cross_board.test_cloud_source_duplicates import choose, upload
from src.api.tests.card_cross_board.test_cloud_source_duplicates import sources as sources
from src.api.tests.card_visibility.conftest import current_card as current_card
from src.api.tests.card_visibility.test_board_cloud import cloud as base_cloud  # noqa: F401


pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


def download(f, file):
    response = FileApi.get_file(
        SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.HumanUI}),
        *file.path.split("/")[-3:],
        service=f.service,
    )
    spool = response.background.func.__self__
    try:
        return spool.read(), response
    finally:
        spool.close()


def test_old_wiki_url_follows_active_bytes_and_revocation_denies_both_locators(sources, monkeypatch):
    f = sources
    monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: f.actor)
    old = f.wiki_file.file.model_copy()
    result = choose(f, upload(f), "wiki")
    assert result["uid"] == f.wiki_file.get_uid()
    delivered, response = download(f, old)
    assert delivered == b"replacement"
    assert response.headers["cache-control"] == "private, no-store"
    assert Storage.get_file(old) == b"original"
    current = f.service.project_wiki.resolve_readable_file(f.actor, old.storage_type, old.storage_name, old.filename)
    assert download(f, current.file)[0] == b"replacement"
    with f.engine.begin() as db:
        db.execute(delete(ProjectWikiAssignedUser))
    for file in (old, current.file):
        with pytest.raises(ApiException.NotFound_404):
            download(f, file)


@pytest.mark.parametrize("change", ["ambiguous", "reparented", "deleted", "cycle"])
def test_historical_wiki_locator_denies_ambiguous_or_invalid_target(sources, monkeypatch, change):
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import ProjectWiki, ProjectWikiAttachment

    f = sources
    monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: f.actor)
    old = f.wiki_file.file.model_copy()
    choose(f, upload(f), "wiki")
    with DbSession.atomic() as db:
        target = db.exec(
            SqlBuilder.select.table(ProjectWikiAttachment).where(ProjectWikiAttachment.id == f.wiki_file.id)
        ).first()
        if change == "ambiguous":
            db.insert(
                ProjectWikiAttachment(
                    project_wiki_id=target.project_wiki_id,
                    user_id=target.user_id,
                    filename=target.filename,
                    file=target.file,
                )
            )
        elif change == "reparented":
            wiki = ProjectWiki(project_id=f.project.id, title="Other readable wiki", is_public=True)
            db.insert(wiki)
            target.project_wiki_id = wiki.id
            db.update(target)
        else:
            if change == "deleted":
                target.deleted_at = SafeDateTime.now()
            else:
                target.replaced_by_id = target.id
            db.update(target)
    with pytest.raises(ApiException.NotFound_404):
        download(f, old)
    assert Storage.get_file(old) == b"original"
