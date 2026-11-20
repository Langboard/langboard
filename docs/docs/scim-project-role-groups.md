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

Retry a failed write with the same external ID and intended membership. A failed
membership write does not leave a partial group. If verification fails after the
group is saved, retrying the same write reconciles access using the saved
membership. If verification fails after deletion, retrying the deletion does not
recreate the group.

Groups are matched by external ID within their directory authority. A matching
display name does not link or merge group memberships.

## Directory authority

Only groups from the configured SCIM issuer can grant SCIM-managed roles.
Groups from another issuer, locally managed groups, and groups whose source is
unresolved cannot grant those roles. Removing a user through SCIM does not
remove their memberships in groups managed by another authority.

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
and `limit`. Search terms are literal,
case-insensitive substrings, including `%`, `_`, and `/`. Groups are ordered by
display name and immutable ID; members are ordered by immutable user ID.

Group search covers display name, issuer, and external ID across every stored
source and issuer. Each group returns its UID, display name, explicit stored
source, issuer, external ID, `source_status` (`local`, `legacy-unresolved`,
`current_authority`, or `other_authority`), and `managed` metadata. Only stored
SCIM groups are marked externally managed. Every group is `read_only` through these endpoints, with `read_members=true` and update/delete/manage-members abilities
false. Source status uses the current normalized SCIM issuer without rewriting
stored provenance. SCIM protocol routes continue to show only their configured
authority; the native directory does not change that boundary.

Membership search covers firstname, lastname, and username. Each member returns
only immutable UID, firstname, lastname, username, and `account_status`
(`active`, `inactive`, or `deleted`). Deleted members retain their UID/status
with names and username redacted. Passwords, tokens, email addresses, and private
profiles are excluded. Counts include inactive and deleted users. Personal address books
are separate from the installation directory.
