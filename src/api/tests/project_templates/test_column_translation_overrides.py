"""Board edits override inherited template text without changing workflow meaning."""

from copy import deepcopy
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Project, ProjectColumn
from langboard_shared.domain.services import DomainService
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import ProjectColumnPublisher
from langboard_shared.tasks.activities import ProjectColumnActivityTask
from langboard_shared.tasks.bots import ProjectColumnBotTask
from sqlalchemy import create_engine


@pytest.mark.parametrize(
    "field,value", [("name", "Custom queue"), ("description", "Custom guidance"), ("description", "")]
)
def test_board_override_clears_only_inherited_translation_field(monkeypatch, field, value):
    engine = create_engine("sqlite://")
    for model in (Project, ProjectColumn):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    dispatched = Mock()
    monkeypatch.setattr(ProjectColumnPublisher, "put_dispather", dispatched)
    monkeypatch.setattr(ProjectColumnActivityTask, "project_column_name_changed", Mock())
    monkeypatch.setattr(ProjectColumnBotTask, "project_column_name_changed", Mock())
    inherited = {
        "en": {"name": "Ready", "description": "Prepared work"},
        "ko": {"name": "준비", "description": "준비된 작업"},
        "ja": {"name": "準備", "description": "準備済み作業"},
    }
    try:
        with DbSession.use(readonly=False) as db:
            project = Project(title="Fixture", owner_id=1)
            db.insert(project)
            column = ProjectColumn(
                project_id=project.id,
                name="Ready",
                description="Prepared work",
                workflow_stage="ready",
                translations=deepcopy(inherited),
            )
            db.insert(column)
        service = DomainService().project_column
        if field == "name":
            assert service.change_name(None, project, column, value)
        else:
            assert service.change_description(project, column, value)
        saved = InfraHelper.get_by_id_like(ProjectColumn, column.id)
        assert getattr(saved, field) == value
        assert saved.workflow_stage == "ready"
        other = "description" if field == "name" else "name"
        for locale in inherited:
            assert not saved.translations[locale].get(field)
            assert saved.translations[locale][other] == inherited[locale][other]
        assert inherited["ko"] == {"name": "준비", "description": "준비된 작업"}
        event, _ = dispatched.call_args.args
        assert event[field] == value
        assert event["translations"] == saved.translations
    finally:
        engine.dispose()
