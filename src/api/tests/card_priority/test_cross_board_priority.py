"""Independent cloned values and source-authorized mirror urgency."""

from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import (
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    CardContentBlock,
    CardRelationship,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    GlobalCardRelationshipType,
    GlobalLabel,
    ProjectLabel,
)
from src.api.tests.card_cross_board.test_card_mirrors import mirror_board as mirror_board


@pytest.mark.parametrize("mirror_board", [None, "postgresql-test"], indirect=True)
def test_clone_retains_priority_and_mirror_source_requires_authority(mirror_board):  # noqa: F811 - imported pytest fixture
    b = mirror_board
    with b.engine.begin() as db:
        if b.engine.dialect.name == "sqlite":
            db.exec_driver_sql("BEGIN")
        for model in (Checklist, Checkitem, CardAttachment, CardContentBlock, CardAssignedUser, GlobalLabel,
                      GlobalCardRelationshipType, ProjectLabel, CardAssignedProjectLabel, CheckitemTimerRecord,
                      CardComment, CardRelationship):
            model.__table__.create(db)
    with DbSession.atomic() as db:
        b.source.priority = "P0"
        db.update(b.source)
    result = b.service.clone_cross_board_card(b.actor, b.source_board, b.source, b.destination, b.column, request_uid=str(uuid4()))
    assert result and result["card"]["priority"] == "P0"
    mirror, payload, _ = b.service.link_cross_board_card(b.actor, b.source_board, b.source, b.destination, b.column)
    assert mirror.priority == "P2"
    assert payload["linked_resource"]["priority"] == "P0"
    # Current source authority is re-read; revoking it removes priority with all source data.
    from langboard_shared.domain.models import ProjectAssignedUser, ProjectRole
    from sqlalchemy import delete
    with DbSession.atomic() as db:
        db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id,
                                                  ProjectAssignedUser.user_id == b.actor.id))
        db.exec(delete(ProjectRole).where(ProjectRole.project_id == b.source_board.id,
                                         ProjectRole.user_id == b.actor.id))
    payload = b.service._get_linked_resource_payloads(b.actor, b.destination, [mirror], include_content=False)[mirror.get_uid()]
    assert payload["status"] == "forbidden" and "priority" not in payload
