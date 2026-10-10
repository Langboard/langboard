# ruff: noqa: F811
"""Registered inbound apps participate in bounded context without provider secrets."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Organization, User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from sqlalchemy import text
from test_app_connection_resources import scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("gate", ["enabled", "disabled", "capability", "resource", "connection", "foreign-owner", "type"])
def test_registered_context_uses_declaration_consent_and_current_owner(board, gate):
    engine = DbEngine.get_main_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            db.execute(text("ALTER TABLE organization ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            db.execute(text("ALTER TABLE organization ADD COLUMN suspended_at TIMESTAMP"))
    else:
        Organization.__table__.create(engine)
    connection, definition, binding, resources, _ = scope(board)
    with DbSession.atomic() as db:
        if gate == "disabled":
            definition.is_enabled = False
            db.update(definition)
        elif gate == "capability":
            definition.declaration = {**definition.declaration, "capabilities": ["cards.create"]}
            db.update(definition)
        elif gate == "resource":
            for row in resources:
                row.access_state = "revoked"
                db.update(row)
        elif gate == "connection":
            connection.state = "disconnected"
            db.update(connection)
        elif gate == "foreign-owner":
            connection.owner_id = 2
            db.update(connection)
            other = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
            other.is_admin = True
            db.update(other)
        elif gate == "type":
            definition.declaration = {**definition.declaration, "resource_types": ["another-type"]}
            db.update(definition)
    service = DomainService()
    try:
        result = service.workflow_stage.get_connection_context(board[1], board[2].get_uid())
        if gate == "enabled":
            assert {item["resource_uid"] for item in result["items"]} == {row.get_uid() for row in resources}
            assert {item["app_key"] for item in result["items"]} == {definition.key}
            assert "credential" not in str(result) and "secret://" not in str(result)
            with DbSession.atomic() as db:
                binding.granted_capabilities = []
                db.update(binding)
            assert service.workflow_stage.get_connection_context(board[1], board[2].get_uid())["items"] == []
        else:
            assert result["items"] == []
    finally:
        service.close()
