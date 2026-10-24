"""Align Decision clocks with native timezone-aware model fields.

Old PostgreSQL timestamp columns discarded the session offset on writes. Their
historical instants cannot be inferred safely when they contain data.
"""

import sqlalchemy as sa
from alembic import op


revision = "dfab456c7890"
down_revision = "cefa345b6789"
branch_labels = None
depends_on = None

_COLUMNS = {
    "decision_case": ("created_at", "updated_at", "deadline"),
    "decision_option": ("created_at", "updated_at"),
    "decision_response": ("created_at", "updated_at", "responded_at"),
}


def _convert(timezone):
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return  # SQLite retains the existing native UTC serialization contract.
    connection.exec_driver_sql(
        "LOCK TABLE decision_case, decision_option, decision_response IN ACCESS EXCLUSIVE MODE"
    )
    inspector = sa.inspect(connection)
    pending = []
    for table, names in _COLUMNS.items():
        for column in inspector.get_columns(table):
            if column["name"] in names and column["type"].timezone != timezone:
                name = column["name"]
                if connection.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table}" WHERE "{name}" IS NOT NULL)')):
                    raise RuntimeError(
                        f"Cannot convert populated Decision clock {table}.{name}; "
                        "historical session timezones require explicit reviewed data repair"
                    )
                pending.append((table, name, column["type"]))
    # Validate every column before altering any type. Empty columns contain no
    # instants to interpret; the explicit UTC cast is independent of session TZ.
    for table, name, previous in pending:
        op.alter_column(
            table,
            name,
            existing_type=previous,
            type_=sa.DateTime(timezone=timezone),
            postgresql_using=f'"{name}" AT TIME ZONE \'UTC\'',
        )
    for table, names in _COLUMNS.items():
        for name in names:
            op.alter_column(table, name, server_default=sa.func.now() if timezone and name != "deadline" else None)


def upgrade():
    _convert(True)


def downgrade():
    _convert(False)
