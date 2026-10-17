"""MCP multi-field updates must roll back together and return primary state."""

from types import SimpleNamespace
import pytest
from langboard.mcp_tools import CardMcp
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from sqlalchemy import column, create_engine, select, table, text


@pytest.mark.parametrize("target", ["checklist", "checkitem"])
@pytest.mark.parametrize("failure", [None, "checked", "response"])
def test_update_commits_fields_and_response_as_one_unit(monkeypatch, target, failure):
    primary = create_engine("sqlite://")
    replica = create_engine("sqlite://")
    for engine in (primary, replica):
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE fixture (title TEXT, checked INTEGER)"))
            connection.execute(text("INSERT INTO fixture VALUES ('Old', 1)"))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: primary)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica)
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda *_: (object(), SimpleNamespace(id=10)))
    callbacks = []
    state = table("fixture", column("title"), column("checked"))
    item = SimpleNamespace(card_id=10, checklist_id=20, title="Old")

    def change_title(*args):
        with DbSession.use(readonly=False) as db:
            db.exec(text("UPDATE fixture SET title=:title").bindparams(title=args[-1]))
            db.after_commit(lambda: callbacks.append("title"))
        return True

    def toggle_checked(*args, desired_checked):
        with DbSession.use(readonly=False) as db:
            db.exec(text("UPDATE fixture SET checked=:checked").bindparams(checked=int(desired_checked)))
            db.after_commit(lambda: callbacks.append("checked"))
            if failure == "checked":
                raise RuntimeError("Checked write failed")
        return True

    def response(*args, **kwargs):
        if failure == "response":
            raise RuntimeError("Projection failed")
        with DbSession.use(readonly=True) as db:
            row = db.exec(select(state.c.title, state.c.checked)).first()
        return [{"uid": "list", "title": row[0], "checkitems": [{"uid": "item", "is_checked": bool(row[1])}]}]

    native = SimpleNamespace(get_by_id_like=lambda _: item, change_title=change_title, toggle_checked=toggle_checked)
    checklist = native if target == "checklist" else SimpleNamespace(get_by_id_like=lambda _: item)
    checklist.get_api_list_by_card = response
    service = SimpleNamespace(checklist=checklist, checkitem=native)
    update = getattr(CardMcp, f"update_card_{target}")
    try:
        if failure:
            with pytest.raises(RuntimeError):
                update("project", "card", "target", None, service, title="New", is_checked=False)
        else:
            result = update("project", "card", "target", None, service, title="New", is_checked=False)
            assert result["checklists"].items[0]["title"] == "New"
            assert result["checklists"].items[0]["checkitems"][0]["is_checked"] is False
        with primary.connect() as connection:
            assert connection.execute(text("SELECT title,checked FROM fixture")).one() == (
                ("Old", 1) if failure else ("New", 0)
            )
        with replica.connect() as connection:
            assert connection.execute(text("SELECT title,checked FROM fixture")).one() == ("Old", 1)
        assert callbacks == ([] if failure else ["title", "checked"])
    finally:
        primary.dispose()
        replica.dispose()
