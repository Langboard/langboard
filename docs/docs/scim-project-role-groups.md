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
provenance. Exit status `0` only means these inventory checks found no blocker;
`migration_authorized` remains false. No records are merged, deleted, assigned,
or granted access by this command.

Existing SCIM groups have no stored source/issuer discriminator. The configured
`SCIM_ISSUER`, a matching name, or a user's identity issuer cannot establish each
group's historical provenance. Preserve group UIDs and membership until an
explicit source mapping and reversible migration are reviewed. Personal
`UserGroup`/`UserGroupAssignedEmail` address books remain separate and must not be
promoted into global UID membership by matching email or group names.
