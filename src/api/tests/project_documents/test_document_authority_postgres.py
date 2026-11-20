"""Committed PostgreSQL authority changes fence native transcript and vector reads."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import pytest
from langboard_shared.domain.models import ProjectAssignedUser, ProjectAttachment, ProjectRole
from langboard_shared.domain.models.ProjectAttachmentDocument import ProjectAttachmentDocument
from langboard_shared.domain.services import ProjectDocumentQuery as query
from langboard_shared.tasks.docling import DocumentVectorQuery
from sqlalchemy import delete, select, update


_spec = spec_from_file_location("postgres_document_fixture", Path(__file__).with_name("test_project_document_read.py"))
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
document = _module.document
mirror_board = _module.mirror_board


def source_rows(b):
    """Read complete source rows independently of the service's detached models."""
    with b.engine.connect() as db:
        return (
            dict(db.execute(select(ProjectAttachment.__table__).where(ProjectAttachment.id == b.attachment.id)).mappings().one()),
            dict(db.execute(select(ProjectAttachmentDocument.__table__).where(
                ProjectAttachmentDocument.attachment_id == b.attachment.id
            )).mappings().one()),
        )


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("revocation", ["membership", "role"])
@pytest.mark.parametrize("boundary", ["between_calls", "during_transcript", "during_vector_query"])
def test_committed_revocation_denies_native_document_results(document, monkeypatch, revocation, boundary):
    b = document
    assert b.engine.dialect.name == "postgresql"
    assert b.source_board.owner_id != b.actor.id and not b.actor.is_admin
    original_service = b.service
    original_document_service = b.documents
    original_actor = b.actor
    original_rows = source_rows(b)

    def read(**kwargs):
        return query.read_project_document(
            b.service, b.actor, b.source_board.get_uid(), b.attachment.get_uid(), **kwargs
        )

    def search():
        return query.search_project_document(
            b.service, b.actor, b.source_board.get_uid(), b.attachment.get_uid(), "alpha"
        )

    def authority_rows():
        with b.engine.connect() as db:
            membership = db.execute(select(ProjectAssignedUser.__table__).where(
                ProjectAssignedUser.project_id == b.source_board.id, ProjectAssignedUser.user_id == b.actor.id
            )).mappings().all()
            roles = db.execute(select(ProjectRole.__table__).where(
                ProjectRole.project_id == b.source_board.id, ProjectRole.user_id == b.actor.id
            )).mappings().all()
            return membership, roles

    membership, roles = authority_rows()
    assert len(membership) == len(roles) == 1 and "read" in roles[0]["actions"]
    assert read().text == b.state.markdown[:4000]
    found = search()
    assert len(found.matches) == 1 and "authorized" in found.matches[0].content
    assert "secret other board" not in found.model_dump_json()

    revoked = False

    def revoke():
        nonlocal revoked
        assert not revoked
        with b.engine.begin() as db:
            if revocation == "membership":
                changed = db.execute(delete(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == b.source_board.id, ProjectAssignedUser.user_id == b.actor.id
                ))
            else:
                changed = db.execute(update(ProjectRole).where(
                    ProjectRole.project_id == b.source_board.id, ProjectRole.user_id == b.actor.id
                ).values(actions=[]))
            assert changed.rowcount == 1
        revoked = True
        membership, roles = authority_rows()
        if revocation == "membership":
            assert membership == [] and "read" in roles[0]["actions"]
        else:
            assert len(membership) == 1 and roles[0]["actions"] == []

    def denied(operation):
        with pytest.raises(ValueError) as failure:
            operation()
        assert str(failure.value) == "Project document unavailable"
        for private in (b.state.markdown[:40], "authorized", "report.pdf", "owned.pdf", b.attachment.get_uid()):
            assert private not in str(failure.value)

    if boundary == "between_calls":
        revoke()
    elif boundary == "during_transcript":
        def after_serialization(**kwargs):
            result = query.ProjectDocumentRead(**kwargs)
            assert result.text
            revoke()
            return result

        denied(lambda: read(read_model=after_serialization))
    else:
        original_search = DocumentVectorQuery.search_vector_generation

        def after_vector_query(*args, **kwargs):
            hits = original_search(*args, **kwargs)
            assert hits and "authorized" in hits[0]["content"]
            revoke()
            return hits

        monkeypatch.setattr(DocumentVectorQuery, "search_vector_generation", after_vector_query)
        denied(search)

    assert revoked
    for _ in range(2):
        denied(read)
        denied(search)
    assert b.service is original_service and b.documents is original_document_service and b.actor is original_actor
    assert source_rows(b) == original_rows
