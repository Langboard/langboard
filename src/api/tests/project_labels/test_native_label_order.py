"""Label allocation repairs malformed order within the unchanged native boundary."""

# ruff: noqa: F811
import pytest
from fastmcp import Client
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import Project, ProjectLabel
from sqlalchemy import select, update
from src.api.tests.card_workspace.test_card_change_counts import counts  # noqa: F401
from src.api.tests.mcp_integration.test_label_action_command import invoke, registered  # noqa: F401
from src.api.tests.project_labels.test_mcp_label_actions import native_labels  # noqa: F401


@pytest.mark.parametrize("operation", ["canonical_local", "canonical_global", "legacy_local", "legacy_global"])
@pytest.mark.parametrize("orders", [(0, 0), (3, 7), (1, 1)])
async def test_consented_malformed_order_preserves_contents_and_other_board(registered, operation, orders):
    from langboard.mcp_integration.Providers import create_native_tool
    from langboard.mcp_integration.Server import McpServer
    from langboard.mcp_integration.Tool import McpTool

    b = registered
    with DbSession.atomic() as db:
        foreign_project = Project(owner_id=b.f.other.id, title="Other")
        db.insert(foreign_project)
        foreign = ProjectLabel(
            project_id=foreign_project.id, name="Foreign", color="#111111", description="Retain", order=9
        )
        db.insert(foreign)
    with b.f.engine.begin() as db:
        for label, order in zip(b.n.labels, orders):
            db.execute(update(ProjectLabel).where(ProjectLabel.id == label.id).values(order=order))
    before = b.n.service.project_label.get_api_list_by_project(b.f.project)
    local = operation.endswith("local")
    if operation.startswith("legacy"):
        name = "create_local_project_label" if local else "use_global_project_label"
        b.server.add_tool(create_native_tool(name, McpTool.get_tool(name), McpServer._wrap_tool))
    async with Client(b.server) as client:
        if operation.startswith("canonical"):
            change = (
                {"action": "create_local", "name": "New order", "explicit_user_request": True}
                if local
                else {"action": "use_global", "global_label_uid": b.unique_global.get_uid()}
            )
            result = await invoke(client, b, change)
        else:
            arguments = {"project_uid": b.f.project.get_uid()}
            arguments.update(
                {"name": "New order", "explicit_user_request": True}
                if local
                else {"global_label_uid": b.unique_global.get_uid()}
            )
            result = await client.call_tool(name, arguments, raise_on_error=False)
    assert not result.is_error, result
    after = b.n.service.project_label.get_api_list_by_project(b.f.project)
    assert [label["uid"] for label in after[:2]] == [label["uid"] for label in before]
    with b.f.engine.connect() as db:
        rows = db.execute(
            select(ProjectLabel.id, ProjectLabel.order)
            .where(ProjectLabel.project_id == b.f.project.id)
            .order_by(ProjectLabel.order, ProjectLabel.id)
        ).all()
        assert [row.order for row in rows] == [0, 1, 2]
        assert db.scalar(select(ProjectLabel.order).where(ProjectLabel.id == foreign.id)) == 9
    for old, new in zip(before, after):
        assert {k: v for k, v in old.items() if k not in {"order", "updated_at"}} == {
            k: v for k, v in new.items() if k not in {"order", "updated_at"}
        }
    b.n.service.project_label.dispatch_created.assert_called_once()


def rows(b):
    with b.f.engine.connect() as db:
        return db.execute(select(ProjectLabel.__table__).order_by(ProjectLabel.id)).all()


def test_repair_insert_rollback_and_after_commit(registered, monkeypatch):
    b = registered
    service = b.n.service.project_label
    with b.f.engine.begin() as db:
        db.execute(update(ProjectLabel).where(ProjectLabel.project_id == b.f.project.id).values(order=8))
    before = rows(b)
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            service.create(b.f.user, b.f.project, "Rollback", "#112233", "Retain")
            assert not service.dispatch_created.called
            raise RuntimeError("rollback")
    assert rows(b) == before
    service.dispatch_created.assert_not_called()
    original = b.f.repo.project_label.insert

    def fail_insert(label):
        original(label)
        raise RuntimeError("insert failed")

    with monkeypatch.context() as patch:
        patch.setattr(b.f.repo.project_label, "insert", fail_insert)
        with pytest.raises(RuntimeError, match="insert failed"):
            service.create(b.f.user, b.f.project, "Failed", "#112233", "Retain")
    assert rows(b) == before
    service.dispatch_created.assert_not_called()
    observed = []
    service.dispatch_created.side_effect = lambda *args: observed.append((args[0], DbSession.has_active_transaction()))
    with DbSession.atomic():
        service.create(b.f.user, b.f.project, "Commit", "#112233", "Retain")
        service.dispatch_created.assert_not_called()
    assert observed == [(b.f.user, False)]
    assert [label["order"] for label in service.get_api_list_by_project(b.f.project)] == [0, 1, 2]


def test_import_override_and_no_effects_preserve_existing_orders(registered):
    b = registered
    service = b.n.service.project_label
    with b.f.engine.begin() as db:
        db.execute(update(ProjectLabel).where(ProjectLabel.project_id == b.f.project.id).values(order=8))
    with DbSession.atomic():
        label, response = service.create(
            b.f.user,
            b.f.project,
            "Imported",
            "#123456",
            "Import description",
            dispatch_effects=False,
            order_override=19,
        )
        assert label.order == response["order"] == 19
    assert [label["order"] for label in service.get_api_list_by_project(b.f.project)] == [8, 8, 19]
    service.dispatch_created.assert_not_called()


def test_postgres_concurrent_local_and_global_allocation(registered):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    b = registered
    if b.f.engine.dialect.name != "postgresql":
        pytest.skip("Row lock concurrency requires PostgreSQL; StaticPool SQLite shares one connection")
    service = b.n.service.project_label
    with b.f.engine.begin() as db:
        db.execute(update(ProjectLabel).where(ProjectLabel.project_id == b.f.project.id).values(order=8))
    barrier = Barrier(6)

    def create(index):
        barrier.wait(timeout=10)
        if index < 3:
            return service.use_global(b.f.user, b.f.project, b.unique_global.get_uid())["created"]
        return service.create(b.f.user, b.f.project, f"Concurrent {index}", "#123456", "") is not None

    with ThreadPoolExecutor(max_workers=6) as pool:
        created = list(pool.map(create, range(6)))
    assert sum(created) == 4
    assert [label["order"] for label in service.get_api_list_by_project(b.f.project)] == list(range(6))
    assert service.dispatch_created.call_count == 4
