# Decision result and execution receipt boundary

This records the remaining evidence gap for Decision Flow UoW-1 as inspected on
2026-10-25. It does not mark the card, migration acceptance, or canary acceptance
complete.

The canonical service is
`src/shared/py/langboard_shared/domain/services/DecisionFlow.py`.

## Existing authoritative boundary

Each `DecisionCase` is an immutable judgment round. A passed decision means its
policy passed; it does not execute the approved Graph plan. Creation attaches the
case through `GraphApprovalRequest.decision_case_id`. The native request continues
to own the immutable execution plan. Decision responses do not dispatch it.

`DecisionHITL.bindable` checks the native approval, run, original requester, card,
interrupt, and execution plan. The native resume claim and completion paths call
`DecisionHITL.validate`, which checks the persisted attachment, current authority,
round, policy, snapshot, target, deadline, and claimed decision receipt. Completion
can record a mutation that already changed the target while retaining the claim,
plan, authority, and lease fences. The dispatch credential check still requires the
original target revision.

## Unsupported remaining slice

The proposal/HITL summary currently emits `effect_result: null` unconditionally.
The Poll summary does so too. The persisted Decision response receipt records this
judgment summary and response order; it is not an execution receipt.

`GraphApprovalRequest.status`, `resolved_at`, and `resolved_by_user_id` record the
native approval's resolution. `InternalBotRun.request_payload.resume_result`
records completion identity and supports replay suppression. Neither model has a
per-tool effect result that proves the approved action actually succeeded. A
completed Graph run or an approved native request alone cannot fill that gap.

Consequently, projecting `approved` or `completed` into `effect_result.success`
would fabricate evidence. The missing slice is an authoritative effect producer
and its persisted correlation to the approval, execution plan, and attempt, followed
by a permission-filtered Decision read projection. The owned Decision service
cannot establish actual tool success from its present inputs. No schema,
repository, execution runtime, or speculative service changes were made for it.

## Local executable evidence

The existing SQLite tests exercise the native requester claim, immutable binding,
replay, authority revocation, lease, completion, and actual card mutation through
the native card repository. They distinguish successful policy passage from native
resume completion. Logs and the exact commands are retained under
`.verification/card-work-wave-20261025/decision-effect-*.log` and
`decision-effect-result.json`.

No new failing product defect was reproduced in the owned boundary. This pass does
not claim a failing-before/passing-after repair, PostgreSQL verification, full
suite success, CI success, same-image canary acceptance, or authenticated live
effect delivery. Existing green checksum and timezone probes were not repeated.
