# Duplicate implementation audit

## Scope and result

The product Python audit compares exact AST function bodies of at least eight
lines in `src/api/langboard` and `src/shared/py/langboard_shared`, excluding
migrations, tests and overload/ellipsis-only signatures. Ten duplicate groups
were found before this change; none remain under that definition. This is not
a claim that all semantically similar or shorter functions have been removed.

The UI audit compares printed, comment-free function bodies of at least eight
lines. The initial 1,502 source files contained 46 candidate groups. The refreshed
1,518 files contain 41 groups. Candidates require caller and closure review;
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

Forty-one groups still need individual ownership and behavior review, including
trigger-condition editing, list refresh/pagination, calendar nodes, form-control
wrappers and integration/settings flows. Shared error callbacks and measurement
markers were removed first. The audit inventory is a prioritization aid, not an
automatic rewrite or deletion rule. Local verification inventories retain the
exact paths for the next pass.

### Follow-up: default bot-trigger scope selector

The two default-scope selectors duplicated the mutation guard, API payload,
translated options, toast/error lifecycle and complete Select markup. They now
share one selector. The existing-scope wrapper only subscribes to the model's
branch UID and passes its current value; the no-scope path uses `custom`.
`custom` still maps to a null default branch in the existing API mutation.
The existing model hook remains unconditional within its wrapper.

The component loses 62 net lines. Option memoization also depends on the
translation function so a language change can refresh the custom option.
Existing design widgets, styles, request/toast behavior and domain boundaries
are retained. Application/tooling TypeScript and targeted ESLint/Prettier pass.
No new abstraction, dependency or implementation-mirroring test is introduced.
This is source refactoring evidence; the updated UI is not yet accepted on the
deployed canary, and the umbrella cleanup card remains incomplete.

## Follow-up: single-list auto-scroll cleanup

Reviewing the remaining drag-and-drop candidates exposed a lifecycle defect in
`createDndSingleRootEvents`. Auto-scroll registration returns cleanup functions;
the implementation spread them into the monitor configuration as unused fields.
The returned teardown removed only the monitor, retaining the scroll registries'
references to each old element. Attachment lists, wiki tabs and board label lists
share this owner and register it from effects that return its teardown.

The existing `combine` now includes those cleanup functions. No new abstraction,
dependency, scroll geometry or reorder behavior was introduced. A real-browser
regression bundles the actual drag-and-drop implementation without mocks and
checks three mount/teardown cycles. The unchanged source leaves the scrollable
attribute registered after all three teardowns; the fix removes it each time.
This is a local browser lifecycle check, not canary publication or evidence that
this defect caused the separately observed API transport cancellations.

## Follow-up: list request and pagination recovery

Review of the remaining list candidates exposed shared lifecycle failures across
six request hooks. A rejected request retained the single-flight lock; four hooks
also advanced their page cursor before success. Shared refresh failures retained
a temporary request limit and reference date. Async timer callbacks in four list
views and the infinite provider left their outer promises pending on rejection.

The existing hooks now release their locks in `finally`, commit page cursors only
after successful processing, and restore refresh parameters on failure. Providers
and the infinite observer release their own gates without consuming a failed page.
Unused pagination delays of 0.5–2.5 seconds and refresh delays of two seconds were
removed. Scheduled initial loads are cancelled during effect cleanup. Existing
API/mutation error reporting remains the owner of user-facing request failures;
no new request framework or dependency was introduced.

Run `node --test scripts/list-loading-lifecycle.test.cjs` from `src/ui`.
The regression executes production hooks/providers with controlled React state
and HTTP boundaries: the unchanged source failed all 12 cases, while the updated
source passes all 12. Both application and tooling TypeScript checks pass through
`npm run typecheck`. These checks cover failure/retry cursors, single-flight
release, refresh rollback and provider/observer gates. They do not exercise React
DOM rendering, initial-effect timing or deployed canary behavior. The separate
measured server-side board latency and transport cancellation remain unresolved.

### Follow-up: current activity scope and zero counts

Both shared list hooks retained a stale new-record count when the server returned
zero. Their count-only requests also omitted the selected activity assignee, and
memoized callbacks could keep the previous URL/assignee after a scope change.
Use the same assignee for count and record reads, include current request inputs
in callback dependencies, and accept numeric zero while retaining an omitted
count. This removes the stale refresh prompt and its unnecessary list request.

The same regression harness now honors callback dependency memoization. Six added
cases fail before this correction and pass afterward (18 total), covering scope
changes, zero counts, omitted counts and no refresh request when the count is zero.
This remains source-level lifecycle evidence, not authenticated canary acceptance.

### Read-only checklist rendering

An unassigned checkitem dereferenced `assignedUser.uid` when the viewer lacked
`card_update`. Optional access prevents the render failure without changing who
can edit: an unassigned item requires card edit permission, while an assigned
item remains editable by its owner. Run
`node --test scripts/checkitem-readonly-render.test.cjs` from `src/ui`.
The actual display component is executed with model/context hooks and inert child
widgets: both null/undefined read-only cases fail before the change, and all five
permission cases pass afterward. This is component-render evidence, not DOM or
deployed browser acceptance.

### Card tray availability snapshots

Availability checks run against a snapshot of the folded-card tray. Previously,
`retain` filtered every current card when that response arrived, so a card added
after dispatch could disappear without being checked. Require the checked UID
set at the existing store boundary, matching the recent-card store's scoped
pruning rule. Both successful responses and board-access denials now remove
only members of that request snapshot. Other users/projects and newly folded
cards remain intact. No cache, hook, transport or dependency was added.

Run `node --experimental-strip-types --test
src/pages/BoardPage/components/card/CardFlipStore.test.ts` from `src/ui`.
The new late-response regression fails against the previous store, then all
four store tests pass after the correction. The existing availability request
still aborts on scope change and ignores malformed/failed responses. These
checks prove store behavior; deployed tray/browser acceptance remains pending.


## Follow-up: unused identity reads in board member classification

Board details classify the already loaded member set through the native SCIM
service. The projection previously read identity links and SCIM groups even
when the configured classification mode could not use them. Keep the current
primary policy/user reads, and read links only for configured SCIM or trusted
OIDC classification. Read SCIM group membership only when SCIM classification
is configured. No identity cache, background reconciliation or new abstraction
is introduced. Explicit SCIM policy still takes precedence over OIDC, including
an empty employee-group selection; inactive users remain unknown.

The real SQLite regression exercises one and 25 members in each mode. OIDC
classification uses three SELECTs instead of four; unconfigured/disabled
classification uses two instead of four. Configured SCIM retains four reads.
The original source fails six of the eight new query-count cases. All 29
focused tests pass after the change, including existing authority-change, native
group-ID, custom bot assignment and board-read bootstrap behavior. This reduces source query work; it does not prove
a deployed latency improvement. Authenticated canary measurement remains pending.

## Shared live timer duration (2026-10-18)

The current primary `Langboard/langboard` main e585b322 and the previous upstream
main 45171c109 are both ancestors of the consolidated canary source. No upstream
merge or architecture replacement was needed for this pass.

The refreshed exact-body UI audit found 43 candidate groups in 1,515 source
files. Reviewing the timer group exposed an actual divergence: TrackingRow
memoized duration only on its tick, omitted start-time corrections from its
effect dependencies, and scheduled another timeout every second even while
stopped or paused. The card-detail timer already handled live fields and only
scheduled work while running.

Both now use `useTimerDuration`, preserving the existing date-fns calculation and
localized formatter. Field changes render immediately; only a running timer with
a start date owns an interval, cleaned on pause, start-date change and unmount.
No shared clock store, extra request, polling service or dependency is added.
The production consumers remove about 60 repeated lines. The inventory remains
a textual candidate list, not proof of semantic duplication or full cleanup.

Browser regression mounts both real timer components with the real model
registry. Two original failures reproduce stale accumulated time and start-time
correction. Candidate passes three checks, including controlled-clock running
progress, pause stability and no recurring timer work while stopped/paused.
The timer instrumentation observes two intervals while both views run and zero
after pause. TypeScript, changed production ESLint, formatting and diff checks
pass. The existing localization workflow includes this suite without adding a
job or runner. Original fixture expectations initially used long English units;
they were corrected to the product's existing narrow units before recording the
failing baseline. Logs remain in `src/ui/.verification/timer-duration-*`.
These are local browser checks, not a measured backend/board latency gain or
accepted canary deployment.


## Shared label collaboration projection (2026-10-18)

The card label display and label picker each maintained the same peer-metadata
reducer and value interface. An extracted execution of the original production
reducer throws TypeError for the string key `constructor`: an inherited property
is treated as a prior label value before reading its timestamp. The shared
`latestLabelStates` uses a prototype-free dictionary and keeps the existing
string/boolean/number validation, complete peer object, latest timestamp and
first-wins tie behavior. It adds no label, changes no ACL or persistence and does
not reinterpret malformed timestamps or label identifiers.

Three focused checks cover ordering/identity retention, invalid values and
prototype-property keys. TypeScript, changed-file ESLint, formatting and diff
checks pass. The existing localization job includes the dependency-free test;
no new job/runner is added. The refreshed exact-body audit reports 41 candidate
groups in 1,518 source files. Original reproduction is retained at
`.verification/label-meta-before-20261018.log`; final checks are in
`src/ui/.verification/label-collaboration-*`. This is a local reducer regression,
not proof of a deployed two-user collaboration flow or the earlier label-related
server loading outage.


## Reference app packaging extraction (2026-10-18)

Three byte-identical reference app `package.mjs` implementations were moved into
the standalone server SDK's packaging subpath. App-specific declarations and
feature flags stay in each app, with no core checkout required. This also fixes
silently omitted optional profiles in installable ZIPs. SDK 0.1.6 uses the existing
Python package validator and introduces no runtime dependency. Sixteen packages
passed actual host validation and declaration parity; source SDK/app tests and
known deployed gaps are recorded in docs/external-app-sdk.md. This extraction is
outside the UI-only exact-body count above, which remains a separate metric.
