# Card work state

Authorized card reads expose a `work_state` projection so clients can distinguish
workflow, progress, review, execution and archive state. Reading this projection
does not modify the card or grant access to another card or linked resource.

Some axes remain unknown. Preserve `null`; in particular,
`active_queue_eligible: null` does not mean the card is ready for execution.

| Field | Meaning |
| --- | --- |
| `version` | Projection format version, currently `1` |
| `workflow_stage` | Explicit column mapping with an available workflow definition; otherwise `null` |
| `completed` | Whether that workflow definition counts the card as completed; otherwise `null` |
| `active_queue_policy` | Active-queue policy of the mapped workflow definition; otherwise `null` |
| `overdue_policy` | Deadline policy of the mapped workflow definition; otherwise `null` |
| `overdue_suppressed` | Whether deadline alerts are suppressed; archived and linked-resource cards are suppressed; missing policy can leave this `null` |
| `verification_state` | `unverified`, `partial`, `verified`, `stale` or `not_required`; checked items alone do not prove review or approval |
| `verification_source_change_seq` | Card change cursor used to evaluate whether reviewer evidence is current |
| `verification` | Current reviewer evidence record, when available |
| `execution_state` | `failed` for current failed/conflicting signal evidence; otherwise the human execution state, or `external_active`/`queued` for running/queued deployment evidence; `null` when no authoritative execution state is available |
| `external_execution_state` | Deployment execution evidence only; a failed check can fail the aggregate `execution_state` while this field remains `null` |
| `human_execution_state` | `human_active` or `paused` for checklist timers, `idle` for linked resources, otherwise `null`; retained independently when external evidence fails |
| `blocker_state` | `blocked` when a known prerequisite prevents execution; otherwise `null`, which does not prove all gates are clear |
| `dependency_state` | Direct prerequisite status and permission-filtered blockers; `clear` covers dependencies only, not input or approval gates |
| `material_kind` | `work`, `reference` or `wiki-like` |
| `lifecycle` | `active` or `archived`, independent of workflow |
| `active_queue_eligible` | `false` for a known exclusion or prerequisite blocker; otherwise `null` until all required gates can be established |
| `checklist_progress` | Ordinary checkitem `total` and `completed` counts, separate from required acceptance criteria |

`reasons` and `state_inconsistency` provide `code`, `message` and `source_ref`.
They explain missing information or inconsistent states without changing data.
Inaccessible prerequisites expose no private title or card identifier. A card in
a completed workflow with unchecked items, or an archived card with a running
timer, can still be reported as inconsistent. These diagnostics do not change
assignees, timer ownership or checklist completion.

## Reviewer evidence

The authenticated `verification-evidence` API requires permission to update the
card, its current `last_change_seq`, the expected previous evidence record UID,
an explicit reviewer decision and references that identify source revision and
environment. A `verified` decision requires a card-level reference and a
reference for every explicitly declared required checkitem. Those items must
belong to the card and currently be checked. The server records reviewer identity.
The card-level reference omits `checkitem_uid` or sets it to null; item references
alone do not satisfy it. A rejected submission does not add a verification record.

Read the current tool schema before constructing a submission. The native
`VerificationEvidence` model bounds `reference` to 1–2048 characters,
`source_revision` and `environment` to 1–128 each, and `checkitem_uid` to
1–64 when present. A submission contains 1–20 evidence entries and at most 100
unique required checkitem UIDs. Keep `environment` to a short label such as
`canary`; put detailed test results and limitations in the referenced artifact.
Do not truncate a revision or evidence reference to fit a bound. Correct an
invalid submission before sending it rather than retrying it unchanged.

For example, after a full read confirms the source sequence, previous record and
checked required item, a native MCP submission has this shape:

```json
{
  "project_uid": "board-from-current-read",
  "card_uid": "card-from-current-read",
  "expected_change_seq": 42,
  "expected_record_uid": null,
  "decision": "verified",
  "required_checkitem_uids": ["checked-item-from-current-read"],
  "evidence": [
    {
      "reference": "https://evidence.example/card-acceptance",
      "source_revision": "reviewed-source-sha",
      "environment": "canary",
      "checkitem_uid": null
    },
    {
      "reference": "https://evidence.example/checkitem-acceptance",
      "source_revision": "reviewed-source-sha",
      "environment": "canary",
      "checkitem_uid": "checked-item-from-current-read"
    }
  ]
}
```

Replace every example value with current authorized readback and actual evidence.
Use a null previous-record UID only when the full read confirms none exists.
An item reference alone does not establish card-wide coverage. The same artifact
may support both scopes only when it actually documents both. The native service still checks
current authority, revisions, item ownership and completion before saving.

A later card change, including a comment, makes prior evidence `stale`.
`verified` describes the reviewer's declared scope; it does not approve a release,
move the card, establish every acceptance policy or clear separate execution gates.
Generic card metadata cannot substitute for reviewer evidence.

The `execute`, `review` and `triage` profiles of `get_card_bundle` omit the
stale verification object and report `stale_verification` in omitted sections.
Their `verification: null` does not mean no previous record exists. Before
submitting replacement evidence, use `get_card_bundle(profile="full")` on the
same card to read the latest record UID and current `last_change_seq`. Pass that
UID as `expected_record_uid` even when its evidence is stale; use `null` only
when the full read confirms there is no previous record. A conflict requires
a fresh read and review of corrected input, not a replay of the rejected call.

For native MCP `record_card_verification_evidence`, an explicit preflight
rejection (such as a foreign, removed or incomplete required checkitem) is
`not_applied`, with `retryable: false` and `next_action: read_and_review`.
The HTTP tool endpoint returns 400 with `VERIFICATION_INPUT_REJECTED` and the
request ID. Read the current card and correct the submission before a new
attempt. The response does not identify inaccessible checkitems. Revision
conflicts remain 409. Other failures, including errors after insertion, retain
an unknown outcome: reconcile the current record before retrying. Recording
evidence is never an automatic retry or release approval mechanism.
