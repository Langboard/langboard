import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text


os.environ.setdefault("PROJECT_NAME", "langboard")
from langboard.routes.settings.Form import SaveWorkflowStageForm  # noqa: E402
from langboard_shared.core.db.DbEngine import DbEngine  # noqa: E402
from langboard_shared.domain.models import (  # noqa: E402
    WorkflowActionRun,
    WorkflowRule,
    WorkflowRuleAction,
    WorkflowRuleCondition,
    WorkflowRuleRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.services.factory.WorkflowStageService import (
    WorkflowStageEditConflict,
    WorkflowStageService,  # noqa: E402
)
from langboard_shared.infrastructure.repositories.factory.WorkflowStageRepository import (
    WorkflowStageRepository,  # noqa: E402
)
from langboard_shared.publishers import AppSettingPublisher


def migration():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261002114000-4afb6de824d3.py"
    spec = importlib.util.spec_from_file_location("workflow_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def registry(monkeypatch, request):
    url = getattr(request, "param", "sqlite://")
    schema = None
    if url == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    if engine.dialect.name == "postgresql":
        schema = "workflow_registry_test_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=5000"})

    def cleanup():
        try:
            if schema:
                with engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            engine.dispose()

    request.addfinalizer(cleanup)
    for model in (WorkflowStageDefinition, WorkflowRule, WorkflowRuleCondition, WorkflowRuleAction, WorkflowRuleRun, WorkflowActionRun):
        model.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE project_column (id BIGINT, workflow_stage TEXT, deleted_at TEXT, is_archive BOOLEAN)")
        )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE card(id BIGINT, project_column_id BIGINT)"))
        connection.execute(text("CREATE TABLE card_relationship(card_id_parent BIGINT, card_id_child BIGINT)"))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    repo = WorkflowStageRepository(lambda _: None, lambda _: None)
    service = WorkflowStageService(lambda _: None, lambda _: None, SimpleNamespace(workflow_stage=repo))
    monkeypatch.setattr(service, "_publish_work_states", Mock())
    monkeypatch.setattr(AppSettingPublisher, "workflow_stages_changed", Mock())
    yield service, engine


def form(**changes):
    return SaveWorkflowStageForm(
        key="released", name="Released", description="Accepted delivery", rules=changes.pop("rules", []), **changes
    ).model_dump()


def test_stale_editor_cannot_overwrite_or_deactivate_latest_stage(registry):
    service, _ = registry
    fields = form()
    stage = service.save(fields)
    uid = stage.get_uid()
    original = service.get_api_list()[0]["revision"]
    service.save({**fields, "name": "Latest", "expected_revision": original}, uid)
    latest = service.get_api_list()[0]
    assert latest["revision"] != original
    publisher = AppSettingPublisher.workflow_stages_changed
    publisher.reset_mock()
    with pytest.raises(WorkflowStageEditConflict):
        service.save({**fields, "name": "Stale", "expected_revision": original}, uid)
    with pytest.raises(WorkflowStageEditConflict):
        service.deactivate(uid, original)
    publisher.assert_not_called()
    service._publish_work_states.assert_not_called()
    saved = service.get_api_list()[0]
    assert saved["name"] == "Latest" and saved["is_active"] is True
    service.deactivate(uid, saved["revision"])
    inactive = service.get_api_list()[0]
    assert inactive["is_active"] is False and inactive["revision"] != saved["revision"]

def test_registry_invalidation_publishes_after_commit_only(registry):
    from langboard_shared.core.db import DbSession

    service, _ = registry
    publisher = AppSettingPublisher.workflow_stages_changed
    with DbSession.atomic():
        stage = service.save(form())
        publisher.assert_not_called()
    publisher.assert_called_once_with()
    publisher.reset_mock()
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            service.save({**form(), "name": "Rolled back"}, stage.get_uid())
            publisher.assert_not_called()
            raise RuntimeError("rollback")
    publisher.assert_not_called()
    with DbSession.atomic():
        service.save({**form(), "description": "Updated guidance"}, stage.get_uid())
        publisher.assert_not_called()
    publisher.assert_called_once_with()
    publisher.reset_mock()
    with DbSession.atomic():
        service.deactivate(stage.get_uid())
        publisher.assert_not_called()
    publisher.assert_called_once_with()

def test_duplicate_and_missing_target_do_not_create(registry):
    service, _ = registry
    service.save(form())
    with pytest.raises(ValueError):
        service.save(form())
    assert service.save(form(), "missing") is None
    assert len(service.get_api_list()) == 1

def test_seed_retries_id_collisions_before_insert(monkeypatch):
    module = migration()
    identifiers = iter([1, 1, 2, 2, 3, 4, 5, 6])
    monkeypatch.setattr(module, "SnowflakeID", lambda: next(identifiers))
    assert [row["id"] for row in module.builtin_rows()] == [1, 2, 3, 4, 5, 6]
    monkeypatch.setattr(module, "SnowflakeID", lambda: 1)
    with pytest.raises(RuntimeError):
        module.builtin_rows()

def test_registry_routes_require_specific_admin_role():
    from langboard.routes.settings import WorkflowStageSettingsApi as api
    from langboard_shared.core.filter import AuthFilter
    from langboard_shared.filter import RoleFilter

    for endpoint, action in [
        (api.get_workflow_stages, "workflow_stage_read"),
        (api.create_workflow_stage, "workflow_stage_create"),
        (api.update_workflow_stage, "workflow_stage_update"),
        (api.deactivate_workflow_stage, "workflow_stage_deactivate"),
    ]:
        assert AuthFilter.get_filtered(endpoint) == "admin"
        _, actions, _, admin_bypass = RoleFilter.get_filtered(endpoint)
        assert actions == [action] and admin_bypass is False

def test_column_usage_counts_explicit_non_deleted_non_archive_bindings(registry):
    service, engine = registry
    service.save(form())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project_column VALUES (1, 'released', NULL, FALSE), (2, 'released', 'deleted', FALSE), (3, 'released', NULL, TRUE), (4, NULL, NULL, FALSE)"
            )
        )
    assert service.get_api_list()[0]["used_column_count"] == 1

def test_policy_projection_targets_bound_cards_and_same_project_dependents(registry):
    service, engine = registry
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE card"))
        connection.execute(
            text("CREATE TABLE card(id BIGINT, project_id BIGINT, project_column_id BIGINT, deleted_at TEXT)")
        )
        connection.execute(
            text("INSERT INTO project_column VALUES (1, 'released', NULL, FALSE), (2, 'active', NULL, FALSE)")
        )
        connection.execute(text("ALTER TABLE project_column ADD COLUMN project_id BIGINT DEFAULT 10"))
        connection.execute(
            text("INSERT INTO card VALUES (1,10,1,NULL),(2,10,2,NULL),(3,20,2,NULL),(4,10,1,'deleted'),(5,10,2,NULL)")
        )
        connection.execute(text("INSERT INTO card_relationship VALUES (1,2),(1,3),(1,2),(4,5)"))
    assert set(service.repo.workflow_stage.get_policy_affected_cards("released")) == {(10, 1), (10, 2)}
    assert service.repo.workflow_stage.get_policy_affected_cards("missing") == []


def native_rule(kinds):
    return {"name": "Entry actions", "is_enabled": True, "trigger": {"kind": "stage_entered"},
        "condition_match": "all", "conditions": [], "actions": [{"kind": kind, "executor": {"kind": "event_actor"},
        "config": {}, "requires_previous_success": False} for kind in kinds]}


@pytest.mark.parametrize("event_type", ["card_moved", "card_comment_deleted", "card_checkitem_deleted"])
def test_supported_card_event_rule_roundtrips(registry, event_type):
    service, _ = registry
    trigger = {"kind": "event", "event_type": event_type}
    service.save(form(rules=[{**native_rule(["timers.stop"]), "trigger": trigger}]))
    assert service.get_api_list()[0]["rules"][0]["trigger"] == trigger


def test_deleted_card_event_has_explicit_validation_error():
    rule = {**native_rule(["timers.stop"]), "trigger": {"kind": "event", "event_type": "card_deleted"}}
    with pytest.raises(ValueError, match="Unsupported workflow event: card_deleted"):
        form(rules=[rule])


@pytest.mark.parametrize("update", [False, True])
def test_deleted_card_event_save_does_not_write_or_publish(registry, update):
    from sqlalchemy import event

    service, engine = registry
    stage = service.save(form(rules=[native_rule(["timers.stop"])]))
    before = service.get_api_list()
    publisher = AppSettingPublisher.workflow_stages_changed
    publisher.reset_mock()
    statements = []

    def record_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_write)
    try:
        fields = {**form(), "name": "Must not save", "rules": [
            {**native_rule(["author.notify"]), "trigger": {"kind": "event", "event_type": "card_deleted"}}
        ]}
        with pytest.raises(ValueError, match="Unsupported workflow event: card_deleted"):
            service.save(fields, stage.get_uid() if update else None)
        assert service.get_api_list() == before
        assert statements == []
        publisher.assert_not_called()
        service._publish_work_states.assert_not_called()
    finally:
        event.remove(engine, "before_cursor_execute", record_write)


@pytest.mark.parametrize("kinds", [["checkitems.complete"], ["timers.stop"], ["author.notify"], ["checkitems.complete", "timers.stop", "author.notify"]])
def test_common_entry_actions_roundtrip_without_retired_fields(registry, kinds):
    service, _ = registry
    stage = service.save(form(rules=[native_rule(kinds)], confirm_descendant_move=True))
    saved = service.get_api_list()[0]
    assert saved["confirm_descendant_move"] is True
    assert not {"entry_effects", "entry_bots", "policy_bots", "continuous_effects"}.intersection(saved)
    assert [action["kind"] for action in saved["rules"][0]["actions"]] == kinds
    expected_ids = [action["uid"] for action in saved["rules"][0]["actions"]]
    service.save({**form(rules=saved["rules"]), "expected_revision": saved["revision"]}, stage.get_uid())
    assert [action["uid"] for action in service.get_api_list()[0]["rules"][0]["actions"]] == expected_ids


@pytest.mark.parametrize("old_field,value", [("entry_effects", []), ("entry_bots", []), ("policy_bots", []), ("continuous_effects", {})])
def test_retired_api_and_service_writes_are_rejected(registry, old_field, value):
    service, _ = registry
    with pytest.raises(ValueError):
        SaveWorkflowStageForm(key="released", name="Released", rules=[], **{old_field: value})
    with pytest.raises(ValueError):
        service.save({**form(), old_field: value})
    assert service.get_api_list() == []


@pytest.mark.parametrize("invalid", [
    {"trigger": {"kind": "event", "event_type": "card_moved", "interval_seconds": 60}},
    {"trigger": {"kind": "event", "event_type": "renamed_old_event"}},
    {"trigger": {"kind": "interval", "interval_seconds": True}},
    {"conditions": [{"kind": "arbitrary_sql", "operator": "eq", "value": True}]},
    {"condition_match": "any"},
    {"actions": [{"kind": "timers.stop", "executor": {"kind": "compactor"}, "config": {}, "requires_previous_success": False}]},
    {"actions": [{"kind": "author.notify", "executor": {"kind": "event_actor"}, "config": {"secret": "unsupported"}, "requires_previous_success": False}]},
])
def test_rule_validation_is_typed_and_rejects_unknown_dialects(registry, invalid):
    with pytest.raises(ValueError):
        form(rules=[{**native_rule(["timers.stop"]), **invalid}])


def test_rule_edit_increments_revision_retains_identity_and_retires_removed_rule(registry):
    from langboard_shared.core.db import DbSession, SqlBuilder
    service, _ = registry
    stage = service.save(form(rules=[native_rule(["timers.stop"])]))
    first = stage.api_response()
    rule = first["rules"][0]
    changed = {**rule, "name": "Renamed"}
    service.save({**form(rules=[changed]), "expected_revision": first["revision"]}, stage.get_uid())
    current = stage.api_response()
    assert current["rules"][0]["actions"] == rule["actions"]
    assert current["rules"][0]["uid"] == rule["uid"]
    assert current["rules"][0]["revision"] == rule["revision"] + 1
    assert current["revision"] != first["revision"]
    service.save({**form(), "expected_revision": current["revision"]}, stage.get_uid())
    with DbSession.use(readonly=False) as db:
        retired = db.exec(SqlBuilder.select.table(WorkflowRule)).first()
        assert retired.retired and not retired.is_enabled
    assert stage.api_response()["rules"] == []


def test_compaction_thresholds_are_explicit_nonzero_or_conditions(registry):
    rule = {"name": "Compact", "is_enabled": True, "trigger": {"kind": "interval", "interval_seconds": 300},
        "condition_match": "any", "conditions": [{"kind": "uncompacted_comments", "operator": "gte", "value": 20}],
        "actions": [{"kind": "comments.compact", "executor": {"kind": "compactor"}, "config": {}, "requires_previous_success": False}]}
    service, _ = registry
    assert service.save(form(rules=[rule])).api_response()["rules"][0]["conditions"] == rule["conditions"]
    for conditions in ([], [{"kind": "uncompacted_comments", "operator": "gte", "value": 0}], [*rule["conditions"], {"kind": "actor_is_not_author", "operator": "eq", "value": True}]):
        with pytest.raises(ValueError):
            form(rules=[{**rule, "conditions": conditions}])
