"""Read-only inventory before the singleton tenant and directory migration.

No names, email addresses, credentials, or connection URLs are emitted. Existing
SCIM groups cannot be attributed to an issuer from the configured issuer alone.
"""

import json
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    IdentityProvider,
    Organization,
    Project,
    ScimGroup,
    ScimGroupMember,
    User,
    UserGroup,
    UserGroupAssignedEmail,
    UserIdentityLink,
)
from langboard_shared.Env import Env
from sqlalchemy import Connection, func, inspect, select


def audit_directory(connection: Connection, configured_issuer: str) -> dict:
    """Use the caller's consistent, read-only snapshot; never migrate records."""

    def count(model, *conditions):
        return connection.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    organizations = count(Organization)
    active_organizations = count(Organization, Organization.is_active.is_(True))
    unbound_projects = count(Project, Project.organization_id.is_(None))
    orphan_projects = count(
        Project,
        Project.organization_id.is_not(None),
        ~select(Organization.id).where(Organization.id == Project.organization_id).exists(),
    )

    def unavailable_owners(model, owner_column):
        return count(
            model,
            ~select(User.id)
            .where(User.id == owner_column, User.activated_at.is_not(None), User.deleted_at.is_(None))
            .exists(),
        )

    unavailable_organization_owners = unavailable_owners(Organization, Organization.owner_user_id)
    unavailable_project_owners = unavailable_owners(Project, Project.owner_id)
    orphan_members = count(
        ScimGroupMember,
        ~select(ScimGroup.id).where(ScimGroup.id == ScimGroupMember.group_id).exists()
        | ~select(User.id).where(User.id == ScimGroupMember.user_id).exists(),
    )
    groups = count(ScimGroup)
    issuer_counts = connection.execute(
        select(UserIdentityLink.issuer, func.count())
        .where(UserIdentityLink.provider == IdentityProvider.Scim)
        .group_by(UserIdentityLink.issuer)
    ).all()
    issuer = configured_issuer.strip().rstrip("/")
    unresolved_links = sum(
        total for stored_issuer, total in issuer_counts if not issuer or stored_issuer.strip().rstrip("/") != issuer
    )
    orphan_address_entries = count(
        UserGroupAssignedEmail,
        ~select(UserGroup.id).where(UserGroup.id == UserGroupAssignedEmail.group_id).exists(),
    )
    orphan_address_groups = count(UserGroup, ~select(User.id).where(User.id == UserGroup.user_id).exists())
    blockers = []
    if organizations > 1:
        blockers.append("multiple_organizations_require_manual_review")
    elif organizations == 0:
        blockers.append("tenant_bootstrap_requires_explicit_mapping")
    elif active_organizations != 1:
        blockers.append("sole_organization_is_inactive")
    if unbound_projects:
        blockers.append("unbound_projects_require_explicit_mapping")
    if orphan_projects:
        blockers.append("orphan_project_organization_references")
    if unavailable_organization_owners:
        blockers.append("unavailable_organization_owners")
    if unavailable_project_owners:
        blockers.append("unavailable_project_owners")
    if orphan_members:
        blockers.append("orphan_scim_memberships")
    provenance_stored = "source" in {column["name"] for column in inspect(connection).get_columns("scim_group")}
    if count(ScimGroup, ScimGroup.source == "legacy-unresolved") if provenance_stored else groups:
        blockers.append("legacy_group_source_and_issuer_require_provenance")
    if unresolved_links:
        blockers.append("scim_identity_issuer_requires_review")
    if orphan_address_entries or orphan_address_groups:
        blockers.append("orphan_personal_address_book_records")
    return {
        "version": 1,
        "read_only": True,
        "migration_authorized": False,
        "counts": {
            "organizations": organizations,
            "active_organizations": active_organizations,
            "projects": count(Project),
            "unbound_projects": unbound_projects,
            "orphan_projects": orphan_projects,
            "unavailable_organization_owners": unavailable_organization_owners,
            "unavailable_project_owners": unavailable_project_owners,
            "scim_groups": groups,
            "scim_memberships": count(ScimGroupMember),
            "orphan_scim_memberships": orphan_members,
            "scim_identity_links": sum(total for _, total in issuer_counts),
            "unresolved_scim_identity_links": unresolved_links,
            "personal_address_groups": count(UserGroup),
            "personal_address_entries": count(UserGroupAssignedEmail),
            "orphan_personal_address_groups": orphan_address_groups,
            "orphan_personal_address_entries": orphan_address_entries,
        },
        "blockers": blockers,
        "personal_address_book_policy": "preserve_separately_no_uid_membership_promotion",
    }


def main() -> int:
    engine = DbEngine.get_main_engine()
    with engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        try:
            with connection.begin():
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                elif connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("BEGIN")
                    connection.exec_driver_sql("PRAGMA query_only = ON")
                report = audit_directory(connection, Env.SCIM_ISSUER or "")
        finally:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only = OFF")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 2 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
