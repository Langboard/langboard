"""Current main board history authority and bounded query regression."""

from datetime import timedelta
import pytest
from jwt import encode
from langboard_shared.core.db import EditorContentModel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardComment,
    Organization,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.Env import Env
from sqlalchemy import event, update
from sqlalchemy.orm import Session
from test_chat_history_authority import history_db  # noqa: F401
from langboard_graph.core.graph.default.history import collect_card_comment_messages, collect_project_activity_messages


@pytest.mark.parametrize("kind", ["comments", "activities"])
@pytest.mark.parametrize(
    "case",
    [
        "owner",
        "read",
        "all",
        "write_only",
        "role_revoked",
        "membership_revoked",
        "deactivated",
        "foreign_card",
        "missing_token",
        "bot",
        "deleted_comment",
    ],
)
def test_current_board_authority_is_in_one_content_query(history_db, kind, case):  # noqa: F811
    for model in (
        Organization,
        Project,
        ProjectColumn,
        Bot,
        Card,
        ProjectActivity,
        ProjectAssignedUser,
        ProjectRole,
        CardComment,
    ):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add_all([Project(id=30, owner_id=1, title="Own board"), Project(id=31, owner_id=1, title="Other board")])
        db.commit()
        db.add_all([ProjectColumn(id=40, project_id=30, name="Own"), ProjectColumn(id=41, project_id=31, name="Other")])
        db.commit()
        db.add_all(
            [
                Card(id=50, project_id=30, project_column_id=40, title="Own"),
                Card(id=51, project_id=31, project_column_id=41, title="Other"),
            ]
        )
        db.add(ProjectAssignedUser(id=70, project_id=30, user_id=2))
        db.add(
            ProjectRole(
                id=71,
                project_id=30,
                user_id=2,
                actions=["*"] if case == "all" else ["card_write"] if case == "write_only" else ["read"],
            )
        )
        db.commit()
        for offset, card_id in enumerate((50, 51)):
            db.add(
                CardComment(
                    id=60 + offset,
                    card_id=card_id,
                    user_id=1,
                    content=EditorContentModel(content="Own" if offset == 0 else "Foreign"),
                    deleted_at=SafeDateTime.now() if case == "deleted_comment" else None,
                )
            )
            db.add(
                ProjectActivity(
                    id=80 + offset,
                    project_id=30 + offset,
                    project_column_id=40 + offset,
                    card_id=card_id,
                    activity_type=ProjectActivityType.CardCreated,
                    activity_history={"title": "Own" if offset == 0 else "Foreign"},
                )
            )
        db.commit()
    actor = 1 if case in {"owner", "foreign_card", "deleted_comment"} else 2
    token = encode(
        {
            "sub": str(actor),
            "iss": Env.PROJECT_NAME,
            "internal": "bot",
            "exp": int((SafeDateTime.now() + timedelta(minutes=5)).timestamp()),
        },
        Env.JWT_SECRET_KEY,
        algorithm=Env.JWT_ALGORITHM,
    )
    variables = {
        "project_uid": SnowflakeID(30).to_short_code(),
        "current_runner_type": "bot" if case == "bot" else "user",
        "app_api_token": "" if case == "missing_token" else token,
    }
    rest = {"card_uid": SnowflakeID(51 if case == "foreign_card" else 50).to_short_code()}

    def collect():
        return (
            collect_card_comment_messages(rest, 5, variables)
            if kind == "comments"
            else collect_project_activity_messages(variables, rest, 5)
        )

    if case.endswith("revoked") or case == "deactivated":
        assert collect()
        with Session(history_db) as db:
            if case == "role_revoked":
                db.execute(update(ProjectRole).where(ProjectRole.id == 71).values(actions=["card_write"]))
            elif case == "membership_revoked":
                db.query(ProjectAssignedUser).filter_by(id=70).delete()
            else:
                db.execute(update(User).where(User.id == 2).values(activated_at=None))
            db.commit()
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(history_db, "before_cursor_execute", record)
    try:
        result = collect()
    finally:
        event.remove(history_db, "before_cursor_execute", record)
    allowed = case in {"owner", "read", "all", "deleted_comment"}
    assert len(result) == int(allowed)
    assert len(queries) == (0 if case in {"missing_token", "bot"} else 1)
    if allowed:
        content = result[0]["content"]
        assert (
            content is None
            if kind == "comments" and case == "deleted_comment"
            else (content == "Own" if kind == "comments" else content["history"]["title"] == "Own")
        )
