"""Board-local work groups. Team membership never confers authorization."""

from typing import Any
from sqlalchemy import JSON, CheckConstraint, ForeignKeyConstraint, UniqueConstraint, event
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Project import Project
from .ProjectAssignedUser import ProjectAssignedUser


class BoardTeam(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_board_team_project_name"),
        CheckConstraint("revision >= 1", name="ck_board_team_revision"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="ck_board_team_name"),
    )
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    name: str = Field(nullable=False, max_length=200)
    revision: int = Field(default=1, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["project_id", "name", "revision"]


class BoardTeamMember(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_board_team_member"),
        ForeignKeyConstraint(["team_id"], ["board_team.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    team_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)
    user_id: SnowflakeID = SnowflakeIDField(nullable=False, index=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["team_id", "user_id"]


class BoardTeamCommand(BaseDbModel, table=True):
    """Immutable command receipts survive Team deletion; never a permission grant."""

    __table_args__ = (UniqueConstraint("actor_id", "request_id", name="uq_board_team_command_request"),)
    actor_id: int = SnowflakeIDField(nullable=False)
    project_id: int = SnowflakeIDField(nullable=False)
    request_id: str = Field(nullable=False, max_length=36)
    fingerprint: str = Field(nullable=False, max_length=64)
    result: dict[str, Any] = Field(nullable=False, sa_type=JSON)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["actor_id", "project_id", "request_id"]


def team_constraint_statements(dialect):
    """Validate writes and clean native lifecycle changes. Command locks provide race safety."""
    valid = """EXISTS (SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id
        JOIN "user" u ON u.id = {user_id} WHERE t.id = {team_id}
        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL
        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a
            WHERE a.project_id = p.id AND a.user_id = u.id)))"""
    new_valid = valid.format(user_id="NEW.user_id", team_id="NEW.team_id")
    scopes = {
        "user": ("user", "activated_at, deleted_at", "m.user_id = OLD.id"),
        "assignment_delete": (
            "project_assigned_user",
            None,
            "m.user_id = OLD.user_id AND t.project_id = OLD.project_id",
        ),
        "assignment_update": (
            "project_assigned_user",
            "project_id, user_id",
            "m.user_id = OLD.user_id AND t.project_id = OLD.project_id",
        ),
        "project": ("project", "owner_id, deleted_at", "t.project_id = OLD.id"),
    }
    statements = []
    if dialect == "postgresql":
        statements.append(f"""CREATE FUNCTION board_team_validate_member() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NOT {new_valid} THEN RAISE EXCEPTION 'Team member must be an active board participant' USING ERRCODE = '23514'; END IF;
            RETURN NEW; END $$""")
        statements.append(
            "CREATE TRIGGER board_team_validate_member BEFORE INSERT OR UPDATE ON board_team_member FOR EACH ROW EXECUTE FUNCTION board_team_validate_member()"
        )
    elif dialect == "sqlite":
        for operation in ("INSERT", "UPDATE"):
            statements.append(f"""CREATE TRIGGER board_team_validate_{operation.lower()} BEFORE {operation} ON board_team_member
                WHEN NOT {new_valid} BEGIN SELECT RAISE(ABORT, 'Team member must be an active board participant'); END""")
    else:
        raise RuntimeError("Teams require SQLite or PostgreSQL")
    for name, (table, columns, scope) in scopes.items():
        invalid = f"""SELECT m.id FROM board_team_member m JOIN board_team t ON t.id = m.team_id
            WHERE {scope} AND NOT {valid.format(user_id="m.user_id", team_id="m.team_id")}"""
        affected = f"SELECT team_id FROM board_team_member WHERE id IN ({invalid})"
        body = f"""UPDATE board_team SET revision = revision + 1 WHERE id IN ({affected});
            DELETE FROM board_team_member WHERE id IN ({invalid});"""
        operation = f"UPDATE OF {columns}" if columns else "DELETE"
        if dialect == "postgresql":
            statements.append(
                f"CREATE FUNCTION board_team_cleanup_{name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {body} RETURN NULL; END $$"
            )
            statements.append(
                f'CREATE TRIGGER board_team_cleanup_{name} AFTER {operation} ON "{table}" FOR EACH ROW EXECUTE FUNCTION board_team_cleanup_{name}()'
            )
        else:
            statements.append(
                f'CREATE TRIGGER board_team_cleanup_{name} AFTER {operation} ON "{table}" BEGIN {body} END'
            )
    return statements


def drop_team_constraint_statements(dialect):
    tables = {
        "user": "user",
        "assignment_delete": "project_assigned_user",
        "assignment_update": "project_assigned_user",
        "project": "project",
    }
    if dialect == "sqlite":
        return [f"DROP TRIGGER IF EXISTS board_team_cleanup_{name}" for name in tables]
    return (
        [f'DROP TRIGGER IF EXISTS board_team_cleanup_{name} ON "{table}"' for name, table in tables.items()]
        + [f"DROP FUNCTION IF EXISTS board_team_cleanup_{name}()" for name in tables]
        + [
            "DROP TRIGGER IF EXISTS board_team_validate_member ON board_team_member",
            "DROP FUNCTION IF EXISTS board_team_validate_member()",
        ]
    )


def _install_constraints(_target, connection, **_kwargs):
    for statement in team_constraint_statements(connection.dialect.name):
        connection.exec_driver_sql(statement)


def _drop_constraints(_target, connection, **_kwargs):
    for statement in drop_team_constraint_statements(connection.dialect.name):
        connection.exec_driver_sql(statement)


BoardTeamMember.__table__.add_is_dependent_on(ProjectAssignedUser.__table__)
event.listen(BoardTeamMember.__table__, "after_create", _install_constraints)
event.listen(BoardTeamMember.__table__, "before_drop", _drop_constraints)
