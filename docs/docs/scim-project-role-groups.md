# SCIM project role groups

Langboard can project SCIM group membership onto an existing project's access roles. This keeps authentication in OIDC while allowing a directory service to remain authoritative for employee access.

## Identity boundary

- A SCIM user's `externalId` is the stable identity key. Email is mutable profile data.
- `POST /scim/v2/Users` does not attach a new `externalId` to an existing account merely because its email matches. It returns `409 Conflict` instead.
- An administrator can explicitly link a pre-existing account with `PUT /scim/v2/Users/{userUid}` and the intended `externalId`.
- A linked `externalId` cannot later be reassigned or replaced through an ordinary profile update.

## Group contract

Create ordinary SCIM groups as usual, but reserve this `externalId` form for a project role group:

```text
project-role:{projectUid}:{role}
```

Supported roles are:

| Role | Effective access |
| --- | --- |
| `viewer` | Read |
| `contributor` | Read, create cards, and update cards |
| `owner` | All project actions |

If a user appears in more than one role group for the same project, the highest role wins deterministically: `owner`, then `contributor`, then `viewer`.

Once any project role group is written, its combined group membership is authoritative for SCIM-managed users in that project. Langboard immediately reconciles the project membership and role records, then reads them back before returning success.

- Removing a SCIM-managed user from every role group removes that user's project access.
- Deactivating or deleting a SCIM user removes the user from role groups and revokes projected project access.
- Non-SCIM local users and guests are not removed by reconciliation.
- The intrinsic project owner is never demoted or removed by SCIM.
- Project role groups reject users that are not linked to the configured `SCIM_ISSUER`.

Changing or deleting a role group's binding reconciles both the former and current projects. A failed readback returns `503 Service Unavailable`; the stored group remains the desired state, so the same SCIM write can be retried safely.

## Retry a failed membership update

A retry preserves the public group identity and effective membership and roles;
internal membership row identifiers and relationship timestamps may change.
After a failed delete readback, the group remains absent and its grants remain
revoked. Retrying the operation does not recreate the deleted group.

Group creation stores the group and its requested membership in one transaction;
a membership write failure leaves neither a partial group nor partial members.
Group updates and deletion also store directory rows and membership changes in
one transaction, so a directory write failure preserves their previous state.
Overlapping upserts for the same `externalId` converge on the existing group UID
after a creation conflict. This does not match groups by display name or merge
different external identities. Entitlement reconciliation follows the committed
directory write: a readback failure preserves that desired state for retry, and
unrelated database errors from reconciliation are not treated as creation races.

## Inventory before the singleton tenant migration

Before assigning existing boards and directory records to a singleton tenant,
run the read-only audit against the intended primary database or its restored
snapshot, using the normal application database configuration:

```sh
PROJECT_NAME=langboard PYTHONPATH=src/api:src/shared/py \
  uv run --no-sync python scripts/audit_instance_directory.py
```

The JSON report contains aggregate counts and review codes, without names,
email addresses, external IDs, issuer URLs, or credentials. PostgreSQL uses one
repeatable-read, read-only transaction; SQLite uses a read-only snapshot for the
audit and restores the connection's normal write policy afterward. Schema or
connection failures propagate as errors rather than an empty inventory.

Exit status `2` means review is required: multiple organizations (including
inactive ones), no active canonical organization, unbound or orphaned boards,
orphaned memberships/address books, unresolved identity issuers, or legacy group
provenance. Organizations and boards whose owners are missing, inactive, or
soft-deleted also require explicit ownership repair before mapping; the audit
does not select a replacement owner or alter access. Exit status `0` only means
these inventory checks found no blocker;
`migration_authorized` remains false. No records are merged, deleted, assigned,
or granted access by this command.

Before provenance migration, legacy SCIM groups have no stored source/issuer discriminator. The configured
`SCIM_ISSUER`, a matching name, or a user's identity issuer cannot establish each
group's historical provenance. Preserve group UIDs and membership until an
explicit source mapping and reversible migration are reviewed. Personal
`UserGroup`/`UserGroupAssignedEmail` address books remain separate and must not be
promoted into global UID membership by matching email or group names.

## Stored group provenance and cutover

Instance groups use the existing `ScimGroup` and `ScimGroupMember` records.
Personal `UserGroup` address books remain separate. Every instance group stores
one of `legacy-unresolved`, `local`, or `scim` as its source. The default is
`legacy-unresolved`; a configured issuer never assigns historical provenance.
SCIM groups require a nonempty issuer normalized by trimming whitespace and
trailing slashes. Local groups require an empty issuer and no external ID;
unresolved groups keep their historical external ID and an empty issuer.
External IDs are unique within `(source, issuer)`, allowing independent SCIM
authorities to use the same external ID without sharing groups or members.

SCIM list/filter/UID lookups, writes, race recovery, employee group settings,
classification, member queries, and project role projection only consider
`source=scim` with the current normalized `SCIM_ISSUER`. Deactivation removes
only memberships in those groups. Local, unresolved, and other issuer groups
cannot grant employee status or SCIM project roles. Their memberships survive
deactivation. Native local group CRUD and UI are a later scope.

Migration `e9143f63c187` fails before DDL when existing groups lack a complete
reviewed mapping. A migration runner must set
`connection.info["reviewed_group_provenance"]` to a dictionary keyed by the
string database group ID, each containing explicit `source` and normalized
`issuer`. Include an explicit `legacy-unresolved`/empty issuer entry for each
unresolved record. Review effective access and project roles before authorizing
this cutover: unresolved records become ineligible for SCIM privileges. The
migration does not reconcile, grant, revoke, or infer project roles. Group and
membership UIDs and existing data remain intact; provenance metadata is added.
Both legacy external ID unique constraints and unique indexes are removed.

Downgrade fails before DDL if external IDs collide across authorities. For a
populated directory it also requires provenance to be archived and an explicit
`connection.info["reviewed_group_provenance_downgrade"] = True` acknowledgement,
since the old schema cannot retain authority metadata. Restore the archived
mapping for a subsequent upgrade. SQLite's table rebuild requires foreign keys
to be disabled by the runner before its migration transaction; the migration
blocks otherwise to prevent membership cascades. Run `PRAGMA foreign_key_check`
afterward and restore foreign key enforcement. PostgreSQL needs no such suspension.
No production migration or operational cutover is authorized by these changes.

## Native installation-wide directory reads

`GET /settings/groups` and `GET /settings/groups/{group_uid}/members` expose
read-only directory inspection through the existing native authentication and
settings-role middleware. Callers must be active installation administrators
with the distinct `group_read` setting action. This action is excluded from
default settings roles; existing `*` grants and explicit full-administrator
email overrides retain their normal behavior. Ordinary group membership grants
no directory access, employee classification, or project role.

Both endpoints accept `page` (1–1,000,000), `limit` (1–100, default 15), and
`query` (at most 200 characters), returning `records`, `total_records`, `page`,
and `limit`. Counts and pagination execute in SQL. Search terms are literal,
case-insensitive substrings, including `%`, `_`, and `/`. Groups are ordered by
display name and immutable ID; members are ordered by immutable user ID.

Group search covers display name, issuer, and external ID across every stored
source and issuer. Each group returns its UID, display name, explicit stored
source, issuer, external ID, `source_status` (`local`, `legacy-unresolved`,
`current_authority`, or `other_authority`), and `managed` metadata. Only stored
SCIM groups are marked externally managed. Every group is `read_only` in this
increment, with `read_members=true` and update/delete/manage-members abilities
false. Source status uses the current normalized SCIM issuer without rewriting
stored provenance. SCIM protocol routes continue to show only their configured
authority; the native directory does not change that boundary.

Membership search covers firstname, lastname, and username. Each member returns
only immutable UID, firstname, lastname, username, and `account_status`
(`active`, `inactive`, or `deleted`). Deleted members retain their UID/status
with names and username redacted. Passwords, tokens, email addresses, and private
profiles are neither selected nor returned by the member page query. Counts
include inactive and deleted users, matching the page projection. Personal
`UserGroup` and `UserGroupAssignedEmail` address books are never queried or
promoted into this directory. Native group CRUD, editing external membership,
UI delivery, and singleton tenant cutover remain subsequent increments.
