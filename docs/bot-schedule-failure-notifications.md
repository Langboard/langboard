# One-time bot schedule failure notifications

A native one-time schedule entering `failed` or `uncertain` produces a
`bot_schedule_failed` web notification. `accepted`
means admission only; this notification does not assert execution success.
Recurring schedules retain their existing behavior. Timeout uncertainty never
permits automatic replay of an external effect.

## Recipient and visibility

The **current project owner** is the operational fallback, consistent with
scheduled notification rules. This is
a distinct notification kind, not a scheduled rule match. The current owner's
account must exist, be active and not deleted. Existing native recipient
resolution checks current project membership and every referenced card using
the API visibility channel. Private cards are denied even for their owner on
this channel. Deleted projects, columns, cards, associations and stale edited
occurrences fail closed. A denied recipient is retried later; no source content
is emitted. A replacement owner is considered only through current authority.

Only project and readable card references plus occurrence hash, schedule UID,
state, attempt/limit and an allowlisted error code enter the notification.
The current column UID is retained for subscription checks at delayed delivery.
Requests, responses, tokens, provider error text and whisper/comment content
are never copied. Column subscriptions are still checked even though a column
is not an outbound notification reference. The native web unsubscription rules
apply; opted-out notifications retain a hidden durable receipt without web
fanout. No separate email service, new email template or email delivery is added.
Notification labels are available in English, Korean, Japanese and Chinese.

## Delivery and recovery

A failure notification is persisted once per occurrence. Its receipt survives
deletion of the visible notification. A notification insertion failure preserves
the terminal schedule outcome and defers delivery by five minutes. Missing or
denied sources and recipients also defer delivery by five minutes. Recovery does
not create a new occurrence, dispatch bot work or increment admission attempts.
A schedule that is no longer Started or whose configuration changed is suppressed.

Delayed delivery rechecks the current account, project owner, card visibility,
column and web subscriptions before publication. Failed web fanout remains in the
notification recovery queue. Persistence is exactly once per occurrence; network
delivery retains the native pipeline's delivery semantics. There is no guarantee
of exactly-once socket transport. Existing dispatch leases that expire become
uncertain and are notified without replaying the bot request.
