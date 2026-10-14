# Card visibility transitions

The card detail header exposes a visibility control to internal members with
the existing `card_update` permission. The server supplies
`can_change_visibility`; clients must not infer permission from labels or
card types.

- `INTERNAL` (Whisper) can become `SHARED` (Public) only through an authenticated
  native user session with explicit disclosure confirmation.
- `SHARED` can become `INTERNAL` for an authorized internal member.
- `PRIVATE` is a creation-only visibility and cannot be converted.
- Public means visible to authorized board members, including external members;
  it does not grant anonymous access.

`PUT /board/{project_uid}/card/{card_uid}/visibility` accepts `visibility`,
`expected_visibility`, and `confirmed`. The service locks the card and checks
current primary-database membership, account status, and update permission.
A stale confirmation returns 409; a forbidden transition returns 403.
Successful changes persist a `CardVisibilityChange` audit record in the same
transaction. Existing content and card relationships remain intact.

The existing board details event carries only
`{"card_visibility_changed": true}`. Connected clients invalidate board card
lists and open details and re-read them through the existing visibility policy.
The notification exposes no card identifier or content. Failed client requests
leave the previous visibility intact and show a retryable error.

## Verification

Server regression tests live in
`src/api/tests/card_visibility/test_visibility_transition.py` alongside the
existing visibility and private-card policy tests. The UI fixture
`BoardCardVisibility.fixture.html` mounts the actual editor control and provider
with a deterministic request adapter for desktop/mobile confirmation, failure,
and permission-state checks. A deployed acceptance check must separately verify
native-session transitions and external-user list/detail/relationship access.

## Narrow canary delivery

The protected canary already provides the visibility policy and audit model. This delivery changes only the card editor, API transition boundary, translations and focused regression coverage. It does not require the pending Phoenix transport migration. Existing upstream migration pull requests remain separate and preserved.

The source-owned build contract defines independent `api` and `ui` profiles using the existing digest-pinned production Dockerfiles. Result-only validation targets report their exact exit codes; API validation covers route authorization, MCP access and visibility transitions. The UI production build includes TypeScript checking. Publication still requires the approved protected canary tip and central release authorization. Review validation cannot publish a product image.

### Dependency and validation boundary

The API Dockerfile installs locked third-party dependencies from package metadata before copying application source. Application edits therefore do not invalidate that dependency layer. UV uses a BuildKit package cache, and the runtime uses one `/app/.venv` rather than duplicating dependencies in a shared-package environment. Existing runtime extras, non-root identity, CJK font installation and scheduled worker entrypoints remain available. API validation adds its development dependencies before source and executes the same focused tests as a non-root user, without installing the unrelated runtime cron daemon.

These are source-layer corrections. Fresh validation remains mandatory. Actual build-duration improvement requires another central run; local dependency resolution is not deployment proof.
