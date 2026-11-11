"""Reviewed visibility transitions with owner-scoped private audit evidence.

Generated with db:migrate (autogenerate=True) from fabc234d5678 on PostgreSQL18.6.
Retains only the generated reviewed_updated_at column operation; unrelated FK
and projection drift was removed. CHECK and trigger operations below are reviewed
manual additions because Alembic does not autogenerate those changes.
"""

from alembic import op
import sqlalchemy as sa

revision = "de2ad7f6e055"
down_revision = "fabc234d5678"
branch_labels = None
depends_on = None


def _dialect():
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("Reviewed visibility requires SQLite or PostgreSQL")
    return dialect


def _drop_guard(dialect):
    if dialect == "sqlite":
        op.execute("DROP TRIGGER card_private_immutable")
    else:
        op.execute("DROP TRIGGER card_private_immutable ON card")
        op.execute("DROP FUNCTION guard_card_private_immutable()")


def _create_guard(dialect, reviewed):
    distinct = "IS NOT" if dialect == "sqlite" else "IS DISTINCT FROM"
    changed = (
        f"OLD.visibility {distinct} NEW.visibility OR "
        f"OLD.owner_user_id {distinct} NEW.owner_user_id OR "
        f"OLD.created_by_user_id {distinct} NEW.created_by_user_id OR "
        f"OLD.created_by_bot_id {distinct} NEW.created_by_bot_id"
    )
    condition = f"(OLD.visibility = 'PRIVATE' OR NEW.visibility = 'PRIVATE') AND ({changed})"
    if reviewed:
        invalid = f"""
            OLD.created_by_user_id {distinct} NEW.created_by_user_id OR
            OLD.created_by_bot_id {distinct} NEW.created_by_bot_id OR
            NOT (NEW.updated_at > OLD.updated_at) OR
            EXISTS (
                SELECT 1 FROM card_relationship r
                WHERE r.card_id_parent = OLD.id OR r.card_id_child = OLD.id
            ) OR
            NOT EXISTS (
                SELECT 1 FROM card_visibility_change a
                WHERE a.card_id = OLD.id
                  AND a.previous_visibility = OLD.visibility
                  AND a.next_visibility = NEW.visibility
                  AND a.changed_by_user_id = COALESCE(OLD.owner_user_id, OLD.created_by_user_id)
                  AND a.changed_by_bot_id IS NULL
                  AND a.channel IN ('human_ui','mcp')
                  AND a.reviewed_updated_at = OLD.updated_at
                  AND a.created_at > OLD.updated_at
                  AND a.created_at <= NEW.updated_at
            )
        """
        condition = f"({condition}) AND ({invalid})"
    if dialect == "sqlite":
        op.execute(f"""
            CREATE TRIGGER card_private_immutable BEFORE UPDATE ON card
            WHEN {condition}
            BEGIN SELECT RAISE(ABORT, 'Card visibility transition is not allowed'); END
        """)
    else:
        op.execute(f"""
            CREATE FUNCTION guard_card_private_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF {condition} THEN
                    RAISE EXCEPTION 'Card visibility transition is not allowed' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER card_private_immutable BEFORE UPDATE ON card
            FOR EACH ROW EXECUTE FUNCTION guard_card_private_immutable()
        """)


def upgrade():
    dialect = _dialect()
    _drop_guard(dialect)
    with op.batch_alter_table("card_visibility_change") as batch:
        batch.add_column(sa.Column("reviewed_updated_at", sa.DateTime(timezone=True), nullable=True))
        for name in ("visibility_change_previous", "visibility_change_next", "visibility_change_human_share"):
            batch.drop_constraint(name, type_="check")
        for name, column in (("visibility_change_previous", "previous_visibility"), ("visibility_change_next", "next_visibility")):
            batch.create_check_constraint(name, f"{column} IN ('INTERNAL','SHARED','PRIVATE')")
        batch.create_check_constraint(
            "visibility_change_reviewed_share",
            "next_visibility <> 'SHARED' OR (changed_by_bot_id IS NULL AND "
            "(channel = 'human_ui' OR (channel = 'mcp' AND reviewed_updated_at IS NOT NULL)))",
        )
        batch.create_check_constraint(
            "visibility_change_reviewed_private",
            "(previous_visibility <> 'PRIVATE' AND next_visibility <> 'PRIVATE') OR "
            "(channel IN ('human_ui','mcp') AND changed_by_user_id IS NOT NULL "
            "AND changed_by_bot_id IS NULL AND reviewed_updated_at IS NOT NULL)",
        )
    _create_guard(dialect, reviewed=True)


def downgrade():
    dialect = _dialect()
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM card_visibility_change WHERE previous_visibility = 'PRIVATE' "
        "OR next_visibility = 'PRIVATE' OR (next_visibility = 'SHARED' AND channel <> 'human_ui') LIMIT 1"
    )).first():
        raise RuntimeError("Reviewed visibility audit records must be retained before downgrade")
    _drop_guard(dialect)
    with op.batch_alter_table("card_visibility_change") as batch:
        for name in ("visibility_change_previous", "visibility_change_next", "visibility_change_reviewed_share", "visibility_change_reviewed_private"):
            batch.drop_constraint(name, type_="check")
        for name, column in (("visibility_change_previous", "previous_visibility"), ("visibility_change_next", "next_visibility")):
            batch.create_check_constraint(name, f"{column} IN ('INTERNAL','SHARED')")
        batch.create_check_constraint(
            "visibility_change_human_share",
            "next_visibility <> 'SHARED' OR (channel = 'human_ui' AND changed_by_bot_id IS NULL)",
        )
        batch.drop_column("reviewed_updated_at")
    _create_guard(dialect, reviewed=False)
