"""Store reviewed group provenance without inferring historical authority."""

import sqlalchemy as sa
from alembic import op


revision = "e9143f63c187"
down_revision = "d8032e52b076"
branch_labels = None
depends_on = None


def _check_sqlite_batch_safety(connection):
    # SQLite rebuilds the parent table. Foreign-key cascades during that rebuild
    # would delete memberships, so require the migration runner to suspend them
    # before its transaction and verify foreign_key_check after the migration.
    if connection.dialect.name == "sqlite" and connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("SQLite group migration requires foreign keys disabled before its transaction")


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, external_id FROM scim_group")).all()
    # The caller supplies a reviewed mapping on this migration connection. No
    # environment, user identity, or group name is evidence of group provenance.
    mapping = connection.info.get("reviewed_group_provenance", {})
    if rows and set(mapping) != {str(row.id) for row in rows}:
        raise RuntimeError("Group provenance cutover blocked: complete reviewed mapping required")
    for row in rows:
        entry = mapping[str(row.id)]
        source, issuer = entry.get("source"), entry.get("issuer")
        if source not in ("legacy-unresolved", "local", "scim") or not isinstance(issuer, str):
            raise RuntimeError("Invalid reviewed group provenance")
        if (
            (source == "scim" and (not issuer or issuer != issuer.strip().rstrip("/")))
            or (source != "scim" and issuer != "")
            or (source == "local" and row.external_id is not None)
        ):
            raise RuntimeError("Invalid reviewed group provenance shape")
    _check_sqlite_batch_safety(connection)
    inspector = sa.inspect(connection)
    unique_names = [
        constraint["name"]
        for constraint in inspector.get_unique_constraints("scim_group")
        if constraint["column_names"] == ["external_id"]
    ]
    indexes = [
        index["name"]
        for index in inspector.get_indexes("scim_group")
        if index["column_names"] == ["external_id"] and not index.get("duplicates_constraint")
    ]
    with op.batch_alter_table("scim_group", reflect_kwargs={"resolve_fks": False}) as batch:
        for name in indexes:
            batch.drop_index(name)
        for name in unique_names:
            batch.drop_constraint(name, type_="unique")
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="legacy-unresolved"))
        batch.add_column(sa.Column("issuer", sa.String(), nullable=False, server_default=""))
        batch.create_unique_constraint("uq_scim_group_authority_external_id", ["source", "issuer", "external_id"])
        batch.create_check_constraint("ck_scim_group_source", "source IN ('legacy-unresolved', 'local', 'scim')")
        batch.create_check_constraint(
            "ck_scim_group_issuer",
            "(source = 'scim' AND issuer <> '' AND substr(issuer, 1, 1) > ' ' AND substr(issuer, length(issuer), 1) > ' ' AND issuer NOT LIKE '%/') "
            "OR (source IN ('legacy-unresolved', 'local') AND issuer = '')",
        )
        batch.create_check_constraint("ck_scim_group_local_identity", "source <> 'local' OR external_id IS NULL")
        batch.create_index("ix_scim_group_external_id", ["external_id"], unique=False)
    for row in rows:
        entry = mapping[str(row.id)]
        connection.execute(
            sa.text("UPDATE scim_group SET source = :source, issuer = :issuer WHERE id = :id"),
            {"id": row.id, "source": entry["source"], "issuer": entry["issuer"]},
        )


def downgrade():
    connection = op.get_bind()
    collision = connection.execute(
        sa.text(
            "SELECT external_id FROM scim_group WHERE external_id IS NOT NULL "
            "GROUP BY external_id HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if collision:
        raise RuntimeError("Group provenance downgrade blocked: external ID collisions require review")
    if connection.execute(sa.text("SELECT 1 FROM scim_group LIMIT 1")).first() and not connection.info.get(
        "reviewed_group_provenance_downgrade"
    ):
        raise RuntimeError("Group provenance downgrade blocked: archive provenance and review authority cutover")
    _check_sqlite_batch_safety(connection)
    with op.batch_alter_table("scim_group", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.drop_index("ix_scim_group_external_id")
        batch.drop_constraint("uq_scim_group_authority_external_id", type_="unique")
        for name in ("ck_scim_group_local_identity", "ck_scim_group_issuer", "ck_scim_group_source"):
            batch.drop_constraint(name, type_="check")
        batch.drop_column("source")
        batch.drop_column("issuer")
        batch.create_unique_constraint("uq_scim_group_external_id", ["external_id"])
        batch.create_index("ix_scim_group_external_id", ["external_id"], unique=True)
