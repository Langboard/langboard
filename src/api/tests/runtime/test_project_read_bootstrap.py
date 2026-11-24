"""Normal project reads do not repair bot assignments; actual bot use still can."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services.factory.ProjectService import ProjectService


module = import_module(ProjectService.__module__)


@pytest.mark.parametrize("is_setting", [False, True])
def test_project_detail_read_does_not_query_or_insert_default_bots(monkeypatch, is_setting):
    owner = User(id=1, firstname="Project", lastname="Owner", email="owner@example.invalid", password="test-only")
    project = SimpleNamespace(id=SnowflakeID(10), owner_id=owner.id, api_response=lambda: {"uid": "project"})
    repo = SimpleNamespace(
        project_assigned_internal_bot=Mock(get_all_by_project=Mock(return_value=[])),
        internal_bot=Mock(get_default_list=Mock(return_value=[])),
        role=SimpleNamespace(project=SimpleNamespace(get_list=Mock(return_value=[]))),
    )
    service = ProjectService(None, None, repo)
    service.get_api_assigned_user_list = Mock(return_value=[])
    service.get_user_role_actions_by_project = Mock(return_value=["read"])
    service._get_service_by_name = Mock(
        return_value=SimpleNamespace(get_api_invited_user_list_by_project=Mock(return_value=[]))
    )
    scim = SimpleNamespace(classify_members=Mock(return_value={owner.get_uid(): "internal"}))
    labels = SimpleNamespace(get_api_list_by_project=Mock(return_value=[{"name": "existing"}]))
    service._get_service = lambda kind: scim if kind is module.ScimProvisioningService else labels
    monkeypatch.setattr(module.InfraHelper, "get_by_id_like", lambda kind, *args, **kwargs: owner if kind is User else project)

    actual_project, payload = service.get_details(owner, project, is_setting=is_setting)

    assert actual_project is project
    assert payload["all_members"][0]["membership_classification"] == "internal"
    assert payload["labels"] == [{"name": "existing"}]
    assert payload["current_auth_role_actions"] == ["read"]
    assert payload["invited_member_uids"] == []
    assert repo.project_assigned_internal_bot.mock_calls == []
    assert repo.internal_bot.mock_calls == []


@pytest.fixture
def bot_assignments(monkeypatch):
    def bot(uid, kind):
        return SimpleNamespace(id=SnowflakeID(uid), bot_type=kind, api_response=lambda: {"id": uid})

    project = SimpleNamespace(id=SnowflakeID(10))
    chat = bot(100, InternalBotType.ProjectChat)
    editor = bot(101, InternalBotType.EditorChat)
    custom = bot(200, InternalBotType.EditorChat)
    custom_setting = SimpleNamespace(api_response=lambda: {"prompt": "custom"})
    rows = [(custom, custom_setting)]
    assigned = Mock()
    assigned.get_all_by_project.side_effect = lambda project: rows.copy()
    assigned.find_with_internal_bot_by_project_and_type.side_effect = lambda project, kind: next(
        (row for row in rows if row[0].bot_type == kind), None
    )
    assigned.insert.side_effect = lambda setting: rows.append(
        (next(bot for bot in (chat, editor) if bot.id == setting.internal_bot_id), setting)
    )
    defaults = Mock()
    defaults.get_default_list.return_value = [chat, editor]
    repo = SimpleNamespace(project_assigned_internal_bot=assigned, internal_bot=defaults)
    monkeypatch.setattr(module.InfraHelper, "get_by_id_like", lambda *args, **kwargs: project)
    return ProjectService(None, None, repo), repo, project, rows, custom_setting


def test_existing_custom_assignment_does_not_trigger_bootstrap(bot_assignments):
    service, repo, project, rows, custom_setting = bot_assignments
    result = service.get_assigned_internal_bot_by_type(project, InternalBotType.EditorChat)
    assert result == rows[0]
    assert result[1] is custom_setting
    repo.internal_bot.get_default_list.assert_not_called()
    repo.project_assigned_internal_bot.insert.assert_not_called()


def test_missing_assignment_is_created_on_bot_use_without_replacing_custom_settings(bot_assignments):
    service, repo, project, rows, custom_setting = bot_assignments
    result = service.get_assigned_internal_bot_by_type(project, InternalBotType.ProjectChat)
    assert result is not None and result[0].bot_type == InternalBotType.ProjectChat
    assert rows[0][1] is custom_setting
    assert len(rows) == 2
    repo.project_assigned_internal_bot.insert.assert_called_once()
    assert service.get_assigned_internal_bot_by_type(project, InternalBotType.ProjectChat) == result
    repo.internal_bot.get_default_list.assert_called_once()
    repo.project_assigned_internal_bot.insert.assert_called_once()


def test_missing_global_default_keeps_unavailable_bot_unassigned(bot_assignments):
    service, repo, project, rows, _ = bot_assignments
    repo.internal_bot.get_default_list.return_value = []
    assert service.get_assigned_internal_bot_by_type(project, InternalBotType.ProjectChat) is None
    repo.project_assigned_internal_bot.insert.assert_not_called()
    assert len(rows) == 1


def test_bot_settings_still_repairs_missing_defaults_and_keeps_custom_prompt(bot_assignments):
    service, repo, project, rows, _ = bot_assignments
    bots, settings = service.get_api_assigned_internal_bot_list_with_setting_map(project)
    assert len(bots) == len(rows) == 2
    assert settings[InternalBotType.EditorChat.value] == {"prompt": "custom"}
    assert settings[InternalBotType.ProjectChat.value]["use_default_prompt"] is True
    repo.project_assigned_internal_bot.insert.assert_called_once()
