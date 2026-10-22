# Card visibility transitions

The card detail header offers a visibility control to internal board members with
`card_update` permission. Use the server's `can_change_visibility` value to decide
whether to show it.

- `INTERNAL` (Whisper) can become `SHARED` (Public) only through an authenticated
  native user session with explicit disclosure confirmation.
- `SHARED` can become `INTERNAL` for an authorized internal member.
- `PRIVATE` is a creation-only choice; transitions to or from it are forbidden.
- Public cards are visible to authorized board members, including external
  members. Public visibility does not grant anonymous access.

## Change visibility

Send `PUT /board/{project_uid}/card/{card_uid}/visibility` with:

| Field | Value |
| --- | --- |
| `visibility` | Target: `INTERNAL` or `SHARED` |
| `expected_visibility` | The card visibility read before confirmation |
| `confirmed` | `true` for disclosure from `INTERNAL` to `SHARED`; otherwise defaults to `false` |

The server checks current membership, account status and update permission.
A stale `expected_visibility` returns HTTP 409: reload and review before confirming
again. A forbidden transition returns HTTP 403. Success returns `visibility` and
`updated_at`, and records the change in the audit history. Existing content and
card relationships remain intact.

## Refresh connected views

The board details event contains `{"card_visibility_changed": true}` without a
card identifier or content. Refresh card lists and open details through their
normal authorized reads. If a request fails, reload the current visibility before
retrying; a client error alone does not establish the server's current state.
