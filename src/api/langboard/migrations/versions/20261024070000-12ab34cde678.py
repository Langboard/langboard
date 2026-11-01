"""Board-local Teams and retained command receipts; never authorization grants."""

import sqlalchemy as sa
from alembic import op


revision = "12ab34cde678"
down_revision = "b1640a24d001"
branch_labels = None
depends_on = None

_CONSTRAINTS = {
    "postgresql": [
        "CREATE FUNCTION board_team_validate_member() RETURNS trigger LANGUAGE plpgsql AS $$\n"
        "            BEGIN IF NOT EXISTS (SELECT 1 FROM board_team t JOIN project p ON p.id = "
        "t.project_id\n"
        '        JOIN "user" u ON u.id = NEW.user_id WHERE t.id = NEW.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id))) THEN RAISE EXCEPTION 'Team "
        "member must be an active board participant' USING ERRCODE = '23514'; END IF;\n"
        "            RETURN NEW; END $$",
        "CREATE TRIGGER board_team_validate_member BEFORE INSERT OR UPDATE ON board_team_member FOR "
        "EACH ROW EXECUTE FUNCTION board_team_validate_member()",
        "CREATE FUNCTION board_team_cleanup_user() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON "
        "t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN "
        "project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN "
        "project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); RETURN NULL; END $$",
        'CREATE TRIGGER board_team_cleanup_user AFTER UPDATE OF activated_at, deleted_at ON "user" '
        "FOR EACH ROW EXECUTE FUNCTION board_team_cleanup_user()",
        "CREATE FUNCTION board_team_cleanup_assignment_delete() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON "
        "t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); RETURN NULL; END $$",
        'CREATE TRIGGER board_team_cleanup_assignment_delete AFTER DELETE ON "project_assigned_user" '
        "FOR EACH ROW EXECUTE FUNCTION board_team_cleanup_assignment_delete()",
        "CREATE FUNCTION board_team_cleanup_assignment_update() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON "
        "t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); RETURN NULL; END $$",
        "CREATE TRIGGER board_team_cleanup_assignment_update AFTER UPDATE OF project_id, user_id ON "
        '"project_assigned_user" FOR EACH ROW EXECUTE FUNCTION '
        "board_team_cleanup_assignment_update()",
        "CREATE FUNCTION board_team_cleanup_project() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON "
        "t.id = m.team_id\n"
        "            WHERE t.project_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN "
        "project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE t.project_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN "
        "project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); RETURN NULL; END $$",
        'CREATE TRIGGER board_team_cleanup_project AFTER UPDATE OF owner_id, deleted_at ON "project" '
        "FOR EACH ROW EXECUTE FUNCTION board_team_cleanup_project()",
    ],
    "sqlite": [
        "CREATE TRIGGER board_team_validate_insert BEFORE INSERT ON board_team_member\n"
        "                WHEN NOT EXISTS (SELECT 1 FROM board_team t JOIN project p ON p.id = "
        "t.project_id\n"
        '        JOIN "user" u ON u.id = NEW.user_id WHERE t.id = NEW.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id))) BEGIN SELECT RAISE(ABORT, 'Team "
        "member must be an active board participant'); END",
        "CREATE TRIGGER board_team_validate_update BEFORE UPDATE ON board_team_member\n"
        "                WHEN NOT EXISTS (SELECT 1 FROM board_team t JOIN project p ON p.id = "
        "t.project_id\n"
        '        JOIN "user" u ON u.id = NEW.user_id WHERE t.id = NEW.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id))) BEGIN SELECT RAISE(ABORT, 'Team "
        "member must be an active board participant'); END",
        'CREATE TRIGGER board_team_cleanup_user AFTER UPDATE OF activated_at, deleted_at ON "user" BEGIN '
        "UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON t.id = "
        "m.team_id\n"
        "            WHERE m.user_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN project p "
        "ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN project p "
        "ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); END",
        'CREATE TRIGGER board_team_cleanup_assignment_delete AFTER DELETE ON "project_assigned_user" '
        "BEGIN UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON t.id = "
        "m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); END",
        "CREATE TRIGGER board_team_cleanup_assignment_update AFTER UPDATE OF project_id, user_id ON "
        '"project_assigned_user" BEGIN UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT '
        "team_id FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN "
        "board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE m.user_id = OLD.user_id AND t.project_id = OLD.project_id AND NOT EXISTS "
        "(SELECT 1 FROM board_team t JOIN project p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); END",
        'CREATE TRIGGER board_team_cleanup_project AFTER UPDATE OF owner_id, deleted_at ON "project" '
        "BEGIN UPDATE board_team SET revision = revision + 1 WHERE id IN (SELECT team_id FROM "
        "board_team_member WHERE id IN (SELECT m.id FROM board_team_member m JOIN board_team t ON t.id = "
        "m.team_id\n"
        "            WHERE t.project_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN project "
        "p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))));\n"
        "            DELETE FROM board_team_member WHERE id IN (SELECT m.id FROM board_team_member m "
        "JOIN board_team t ON t.id = m.team_id\n"
        "            WHERE t.project_id = OLD.id AND NOT EXISTS (SELECT 1 FROM board_team t JOIN project "
        "p ON p.id = t.project_id\n"
        '        JOIN "user" u ON u.id = m.user_id WHERE t.id = m.team_id\n'
        "        AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND u.activated_at IS NOT NULL\n"
        "        AND (p.owner_id = u.id OR EXISTS (SELECT 1 FROM project_assigned_user a\n"
        "            WHERE a.project_id = p.id AND a.user_id = u.id)))); END",
    ],
}

_DROP_CONSTRAINTS = {
    "postgresql": [
        'DROP TRIGGER IF EXISTS board_team_cleanup_user ON "user"',
        'DROP TRIGGER IF EXISTS board_team_cleanup_assignment_delete ON "project_assigned_user"',
        'DROP TRIGGER IF EXISTS board_team_cleanup_assignment_update ON "project_assigned_user"',
        'DROP TRIGGER IF EXISTS board_team_cleanup_project ON "project"',
        "DROP FUNCTION IF EXISTS board_team_cleanup_user()",
        "DROP FUNCTION IF EXISTS board_team_cleanup_assignment_delete()",
        "DROP FUNCTION IF EXISTS board_team_cleanup_assignment_update()",
        "DROP FUNCTION IF EXISTS board_team_cleanup_project()",
        "DROP TRIGGER IF EXISTS board_team_validate_member ON board_team_member",
        "DROP FUNCTION IF EXISTS board_team_validate_member()",
    ],
    "sqlite": [
        "DROP TRIGGER IF EXISTS board_team_cleanup_user",
        "DROP TRIGGER IF EXISTS board_team_cleanup_assignment_delete",
        "DROP TRIGGER IF EXISTS board_team_cleanup_assignment_update",
        "DROP TRIGGER IF EXISTS board_team_cleanup_project",
    ],
}


def upgrade():
    op.create_table(
        "board_team",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_board_team_project_name"),
        sa.CheckConstraint("revision >= 1", name="ck_board_team_revision"),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="ck_board_team_name"),
    )
    op.create_index("ix_board_team_project_id", "board_team", ["project_id"])
    op.create_table(
        "board_team_member",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("board_team.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_board_team_member"),
    )
    op.create_index("ix_board_team_member_team_id", "board_team_member", ["team_id"])
    op.create_index("ix_board_team_member_user_id", "board_team_member", ["user_id"])
    op.create_table(
        "board_team_command",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_id", name="uq_board_team_command_request"),
    )
    for statement in _CONSTRAINTS[op.get_bind().dialect.name]:
        op.get_bind().exec_driver_sql(statement)


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "LOCK TABLE board_team, board_team_member, board_team_command IN ACCESS EXCLUSIVE MODE"
        )
    if any(
        connection.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        for table in ("board_team", "board_team_member", "board_team_command")
    ):
        raise RuntimeError("Cannot discard retained Teams or command receipts")
    for statement in _DROP_CONSTRAINTS[connection.dialect.name]:
        connection.exec_driver_sql(statement)
    op.drop_table("board_team_member")
    op.drop_table("board_team_command")
    op.drop_table("board_team")
