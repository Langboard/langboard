"""Offline, reviewed singleton tenant maintenance.

Stop application writers before snapshot/plan/apply/rollback. Commands acquire
transaction-scoped database locks and a ten-second lock timeout as a final race
guard. Plans/receipts contain identities and hashes, never business row content.
The full-database rollback guard deliberately rejects any intervening business
change. Archive the receipt before resuming writers. No name-based adoption,
owner replacement, environment bootstrap, or ordinary service bypass occurs.

Workflow (PROJECT_NAME/PYTHONPATH configured as for the existing audit script):
  cutover_instance_tenant.py snapshot --output snapshot.json
  cutover_instance_tenant.py plan --input request.json --output plan.json
  cutover_instance_tenant.py apply --input plan.json --reviewed-sha256 HASH --output receipt.json
  cutover_instance_tenant.py rollback --input receipt.json --reviewed-sha256 HASH --output rollback.json

Request: {"snapshot_sha256": snapshot["sha256"], "revision": snapshot["revision"],
 "tenant": {"uid": explicit_uid, "owner_uid": active_owner_uid,
            "name": explicit_name, "slug": explicit_slug},
 "projects": {every_project_uid: tenant_uid},
 "preserve_inactive_project_owners": [every_inactive_or_deleted_project_owner_uid]}
Use the emitted plan/receipt sha256 for the respective review argument. For an
existing tenant all four tenant fields must exactly match; no edits are implied.
"""

import argparse
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from alembic.script import ScriptDirectory
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import Organization
from sqlalchemy import MetaData, delete, inspect, select, update


# Review binds to the shipped migration head, including later directory changes.
# Alembic rejects multiple heads rather than guessing an installation lineage.
REVISION = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations")).get_current_head()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def uid(value):
    return SnowflakeID(int(value)).to_short_code()


def identifier(value):
    try:
        result = int(SnowflakeID.from_short_code(value))
    except (TypeError, ValueError):
        result = 0
    if result <= 0 or uid(result) != value:
        raise ValueError("Invalid explicit UID")
    return result


@contextmanager
def maintenance(engine):
    """One bounded snapshot/write unit; callers must have stopped writers."""
    with engine.connect() as connection:
        with connection.begin():
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            elif connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
                names = inspect(connection).get_table_names()
                quoted = ", ".join(connection.dialect.identifier_preparer.quote(name) for name in sorted(names))
                if quoted:
                    # SHARE blocks writes, including inserts into currently empty tables.
                    # Use one lock acquisition in deterministic order to avoid write skew.
                    connection.exec_driver_sql(f"LOCK TABLE {quoted} IN SHARE ROW EXCLUSIVE MODE")
            else:
                raise ValueError("Only SQLite and PostgreSQL maintenance is supported")
            yield connection


def _tables(connection):
    metadata = MetaData()
    metadata.reflect(connection)
    return metadata.tables


def snapshot(connection):
    tables = _tables(connection)
    if "alembic_version" not in tables:
        raise ValueError("An exact installed Alembic revision is required")
    revisions = connection.execute(select(tables["alembic_version"].c.version_num)).scalars().all()
    if REVISION is None or revisions != [REVISION]:
        raise ValueError("Installed schema must match the current migration head before data maintenance")
    contents = {}
    inspector = inspect(connection)
    for name, table in sorted(tables.items()):
        rows = [dict(row) for row in connection.execute(select(table)).mappings()]
        contents[name] = {
            "columns": [(column.name, str(column.type), column.nullable) for column in table.columns],
            "indexes": sorted(inspector.get_indexes(name), key=lambda index: index["name"]),
            "rows": sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str)),
        }
    return {
        "revision": REVISION,
        "sha256": digest(contents),
        "organizations": [
            {"uid": uid(row["id"]), "owner_uid": uid(row["owner_user_id"]), "active": row["is_active"]}
            for row in contents["organization"]["rows"]
        ],
        "projects": [
            {
                "uid": uid(row["id"]),
                "owner_uid": uid(row["owner_id"]),
                "organization_uid": uid(row["organization_id"]) if row["organization_id"] is not None else None,
            }
            for row in contents["project"]["rows"]
        ],
    }


def _validate(connection, request):
    if set(request) != {"revision", "snapshot_sha256", "tenant", "projects", "preserve_inactive_project_owners"}:
        raise ValueError("Complete explicit review input required")
    current = snapshot(connection)
    if request["revision"] != current["revision"] or request["snapshot_sha256"] != current["sha256"]:
        raise ValueError("Stale reviewed snapshot or revision")
    tables = _tables(connection)
    if not any(
        index["name"] == "uq_organization_single_active" and index["unique"]
        for index in inspect(connection).get_indexes("organization")
    ):
        raise ValueError("Installed singleton tenant index is required")
    # Validate all stored foreign keys, including unrelated business references.
    for table in tables.values():
        for constraint in table.foreign_key_constraints:
            referred = constraint.referred_table.alias()
            conditions = [element.parent == referred.c[element.column.name] for element in constraint.elements]
            present = [element.parent.is_not(None) for element in constraint.elements]
            missing = ~select(1).select_from(referred).where(*conditions).exists()
            if connection.execute(select(1).select_from(table).where(*present, missing).limit(1)).first():
                raise ValueError("Orphan references require manual review")
    organizations = connection.execute(select(tables["organization"])).mappings().all()
    if len(organizations) > 1:
        raise ValueError("Multiple organizations, including inactive records, require manual review")
    tenant = request["tenant"]
    if not isinstance(tenant, dict) or set(tenant) != {"uid", "owner_uid", "name", "slug"}:
        raise ValueError("Explicit tenant identity, name, slug and active owner required")
    tenant_id, owner_id = identifier(tenant["uid"]), identifier(tenant["owner_uid"])
    if not all(isinstance(tenant[key], str) and tenant[key].strip() for key in ("name", "slug")):
        raise ValueError("Explicit nonempty tenant name and slug required")
    users = {row["id"]: row for row in connection.execute(select(tables["user"])).mappings()}
    owner = users.get(owner_id)
    if owner is None or owner["activated_at"] is None or owner["deleted_at"] is not None:
        raise ValueError("Tenant owner must be an active account")
    if organizations:
        organization = organizations[0]
        if not organization["is_active"] or any(
            organization[key] != value
            for key, value in (
                ("id", tenant_id),
                ("owner_user_id", owner_id),
                ("name", tenant["name"]),
                ("slug", tenant["slug"]),
            )
        ):
            raise ValueError("Existing tenant identity and active owner must match exactly")
    projects = connection.execute(select(tables["project"])).mappings().all()
    if request["projects"] != {uid(row["id"]): tenant["uid"] for row in projects}:
        raise ValueError("Complete exact project UID mapping required")
    inactive = set()
    for project in projects:
        project_owner = users.get(project["owner_id"])
        if project_owner is None:
            raise ValueError("Orphan project owner")
        if project_owner["activated_at"] is None or project_owner["deleted_at"] is not None:
            inactive.add(uid(project["owner_id"]))
        if project["organization_id"] not in (None, tenant_id):
            raise ValueError("Conflicting project organization requires manual review")
    reviewed = request["preserve_inactive_project_owners"]
    if not isinstance(reviewed, list) or len(reviewed) != len(set(reviewed)) or set(reviewed) != inactive:
        raise ValueError("Explicit exact review of preserved inactive project owners required")
    return tables, organizations, projects


def plan(connection, request):
    _tables_value, organizations, projects = _validate(connection, request)
    body = {
        "version": 1,
        "request": request,
        "bootstrap": not organizations,
        "previous_bindings": {
            uid(row["id"]): uid(row["organization_id"]) if row["organization_id"] is not None else None
            for row in projects
        },
    }
    return {**body, "sha256": digest(body)}


def _review(document, reviewed_sha256):
    body = {key: value for key, value in document.items() if key != "sha256"}
    if not reviewed_sha256 or document.get("sha256") != reviewed_sha256 or digest(body) != reviewed_sha256:
        raise ValueError("Exact document sha256 review required")


def apply(connection, document, reviewed_sha256):
    _review(document, reviewed_sha256)
    expected = plan(connection, document["request"])
    if expected != document:
        raise ValueError("Reviewed plan does not match exact current state")
    tables = _tables(connection)
    tenant = document["request"]["tenant"]
    tenant_id = identifier(tenant["uid"])
    if document["bootstrap"]:
        # The chosen stable ID is review input, never a newly allocated identity.
        organization = Organization(
            id=tenant_id,
            name=tenant["name"],
            slug=tenant["slug"],
            owner_user_id=identifier(tenant["owner_uid"]),
            is_active=True,
        )
        connection.execute(
            Organization.__table__.insert().values(
                {column.name: getattr(organization, column.name) for column in Organization.__table__.columns}
            )
        )
    for project_uid in document["previous_bindings"]:
        connection.execute(
            update(tables["project"])
            .where(tables["project"].c.id == identifier(project_uid))
            .values(organization_id=tenant_id)
        )
    body = {"version": 1, "plan": document, "post_snapshot": snapshot(connection)}
    return {**body, "sha256": digest(body)}


def rollback(connection, receipt, reviewed_sha256):
    _review(receipt, reviewed_sha256)
    if snapshot(connection) != receipt["post_snapshot"]:
        raise ValueError("Post-cutover state changed; rollback requires manual review")
    document = receipt["plan"]
    _review(document, document["sha256"])
    tables = _tables(connection)
    for project_uid, previous_uid in document["previous_bindings"].items():
        connection.execute(
            update(tables["project"])
            .where(tables["project"].c.id == identifier(project_uid))
            .values(organization_id=identifier(previous_uid) if previous_uid is not None else None)
        )
    if document["bootstrap"]:
        tenant_id = identifier(document["request"]["tenant"]["uid"])
        for table in tables.values():
            for foreign in table.foreign_keys:
                if (
                    foreign.column.table.name == "organization"
                    and connection.execute(
                        select(1).select_from(table).where(foreign.parent == tenant_id).limit(1)
                    ).first()
                ):
                    raise ValueError("Tenant has business references; deletion refused")
        connection.execute(delete(tables["organization"]).where(tables["organization"].c.id == tenant_id))
    restored = snapshot(connection)
    if restored["sha256"] != document["request"]["snapshot_sha256"]:
        raise ValueError("Exact pre-cutover state was not restored")
    return {"version": 1, "restored_snapshot": restored, "rolled_back_receipt_sha256": receipt["sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=("snapshot", "plan", "apply", "rollback"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewed-sha256")
    args = parser.parse_args(argv)
    document = json.loads(args.input.read_text()) if args.input else None
    # Exclusive output creation prevents clobbering the reviewed rollback archive.
    with args.output.open("x") as output:
        with maintenance(DbEngine.get_main_engine()) as connection:
            if args.operation == "snapshot":
                result = snapshot(connection)
            elif args.operation == "plan":
                result = plan(connection, document)
            elif args.operation == "apply":
                result = apply(connection, document, args.reviewed_sha256)
            else:
                result = rollback(connection, document, args.reviewed_sha256)
            output.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
            output.flush()
            import os

            os.fsync(output.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
