"""Settings reads stay on primary without waiting for unrelated row writers."""

from concurrent.futures import ThreadPoolExecutor
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    AppConnection,
    AppDefinition,
    BoardAppBinding,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppResourceSelection import list_resource_selections
from langboard_shared.domain.services.AppWorkflowMapping import read_workflow_mapping
from langboard_shared.domain.services.BoardWorkflowSettings import read_column_workflow
from sqlalchemy import select, update


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("surface", ["resources", "app_workflow", "column_workflow"])
def test_current_settings_reads_do_not_wait_for_locked_rows(board, binding, surface):
    engine = DbEngine.get_main_engine()
    actor, project = board[1:3]
    column = board[5][0]
    with DbSession.atomic() as db:
        definition = AppDefinition(
            key="github",
            approved_by=actor.id,
            declaration={
                "schema_version": 1,
                "key": "github",
                "version": "1.0.0",
                "name": "Example",
                "description": "Concurrency fixture",
                "capabilities": ["resources.read"],
                "resource_types": ["repository"],
                "workflow_requirements": {"required": ["active"], "optional": ["review"]},
            },
        )
        db.insert(definition)
        connection = AppConnection(app_key="github", owner_id=actor.id, state="connected")
        db.insert(connection)

    def read():
        if surface == "resources":
            return list_resource_selections(actor, project.id, "github", connection.id)
        if surface == "app_workflow":
            return read_workflow_mapping(actor, project.id, "github")
        return read_column_workflow(actor, project.id, column.id)

    # A separate connection holds the actual rows until the read has finished.
    # The previous FOR UPDATE reads would time out, including membership/role,
    # app definition, binding, connection and column locks.
    with ThreadPoolExecutor(max_workers=1) as workers:
        with engine.begin() as writer:
            for model, identifier in (
                (User, actor.id),
                (Project, project.id),
                (ProjectAssignedUser, board[3].id),
                (ProjectRole, board[4].id),
                (AppDefinition, definition.id),
                (BoardAppBinding, binding.id),
                (AppConnection, connection.id),
                (ProjectColumn, column.id),
            ):
                writer.execute(select(model.__table__).where(model.id == identifier).with_for_update()).first()
            assert workers.submit(read).result(timeout=5) is not None
            # Nonlocking must not mean cached authority: the next request sees
            # committed primary revocation despite the unchanged caller model.
            writer.execute(update(User).where(User.id == actor.id).values(activated_at=None))
        with pytest.raises(AppGovernanceDenied):
            workers.submit(read).result(timeout=5)
