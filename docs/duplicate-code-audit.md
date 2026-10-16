# Duplicate implementation audit

## Scope and result

The product Python audit compares exact AST function bodies of at least eight
lines in `src/api/langboard` and `src/shared/py/langboard_shared`, excluding
migrations, tests and overload/ellipsis-only signatures. Ten duplicate groups
were found before this change; none remain under that definition. This is not
a claim that all semantically similar or shorter functions have been removed.

The UI audit compares printed, comment-free function bodies of at least eight
lines. The initial 1,502 source files contained 46 candidate groups. The current
1,503 files contain 44 groups. Candidates require caller and closure review;
matching text is not sufficient authority to combine different workflows.

## Canonical owners

| Repeated behavior | Shared owner | Consumers |
| --- | --- | --- |
| Card activity identity | `ActivityTaskHelper.get_card_activity_params` | Six activity task modules |
| Public content block projection | `CardContentBlockService.public_block` | REST batch/single and MCP writes |
| Metadata-provider connection revision | `AppConnection.edit_revision` | Dokploy and GlitchTip |
| Resource grant revocation | `AppResourceAccess.revoke_connection_resources` | Explicit provider disconnects |
| Governance HTTP error mapping | `routes/settings/AppOperation.py` | Five settings APIs |
| IP whitelist normalization | Existing `core/utils/IpAddress.py` | Bot and API-key services |
| Email validation | `routes/FormValidation.py` | Administrator creation and signup |
| AI run completion | Existing `InternalBotRunService.finish_editor` | Editor and board-chat entrypoints |
| Datetime generation map | Existing model types module | Dataclass and msgspec managers |
| Model initialization | Existing `DataModel.__init__` | TypedDict and union models |
| Error message and forbidden callback | Existing `setupApiErrorHandler` module | 40 callbacks in 37 UI files |
| Cursor measurement marker | Dependency-free `CursorOverlay.ts` | Three collaborative inputs |

GitHub connection revisions intentionally keep their account-based field set;
they are not interchangeable with instance-based metadata-provider revisions.
HTTP status, structured error codes, no-store headers, current authorization,
resource scopes and literal content remain owned by their existing boundaries.
The UI callback creates its message reference per error, preventing a previous
error or cancelled request from reusing another message. Cursor coordinate
models remain separate where multiline and single-line geometry differs.

## Revocation regression

The unchanged Dokploy and GlitchTip implementations both failed an existing
regression when a disconnected connection still had granted resources. Their
early return skipped grant cleanup. Explicit disconnect now revokes stale grants
in the same transaction, including this state. Already-revoked resources do not
advance their access revision on a repeated disconnect. This does not introduce
a background reconciler, provider request, credential deletion or selection reset.

## Verification

- Focused Python integration suite: 300 passed, one intentional skip for the
  PostgreSQL concurrency case instantiated with SQLite; its PostgreSQL case ran.
- Actual disposable local PostgreSQL and SQLite exercised provider authority,
  stale revisions, policy-disabled revocation, workflow and native HTTP paths.
- Twelve generated model outputs (dataclass, msgspec, TypedDict; Python 3.10 and
  3.12; standard collections on/off) match the pre-change bytes and parse.
- Existing signup/admin email errors were checked against pre-change source.
- Twenty UI workload, metadata cancellation, session and localized error tests
  passed. English, Korean, Japanese and Chinese messages retain behavior.
- Changed-file TypeScript diagnostics were compared against the unchanged source:
  29 existing diagnostics, 29 current diagnostics, no added diagnostic. This is
  not a clean full-project typecheck claim.
- Failed baseline and intermediate test logs are retained locally.

These are source-level results. Canary publication, authenticated browser E2E,
GlitchTip readback and product-card acceptance remain separate evidence gates.
The umbrella cleanup card must not be archived from these checks alone.

## Remaining UI candidates

Forty-four groups still need individual ownership and behavior review, including
trigger-condition editing, list refresh/pagination, calendar nodes, form-control
wrappers and integration/settings flows. Shared error callbacks and measurement
markers were removed first. The audit inventory is a prioritization aid, not an
automatic rewrite or deletion rule. Local verification inventories retain the
exact paths for the next pass.
