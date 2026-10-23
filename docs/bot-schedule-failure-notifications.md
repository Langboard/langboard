# One-time bot schedule failure notifications

A native one-time schedule entering `failed` or `uncertain` produces a
`bot_schedule_failed` UserNotification through NotificationService. `accepted`
means admission only; this notification does not assert execution success.
Recurring schedules retain their existing behavior. Timeout uncertainty never
permits automatic replay of an external effect.

## Recipient and visibility

The schedule model has no creator recipient. The **current project owner** is
the operational fallback, consistent with scheduled notification rules. This is
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

## Transaction and recovery contract

For terminal runs only, `BotScheduleRun.next_attempt_at` is the notification
retry cursor. A conditional UPDATE obtains the existing occurrence's write
fence before resolving current authority. Notification insertion and setting
the cursor to `9999-01-01T00:00:00Z` commit together. Concurrent workers cannot
insert twice. The cursor receipt survives notification deletion. No new schema
or migration is needed. Pending and dispatching runs retain admission retry and
lease semantics. Terminal retry exhaustion sets the cursor due immediately.

The original terminal admission commit precedes notification insertion. An
insertion exception rolls the notification transaction back, preserving the
terminal outcome; a separate update defers delivery by five minutes. Denied or
missing source/recipient cases also defer by five minutes. Recovery handles at
most 50 due terminal records per pass, ordered by cursor and ID. It never
creates an occurrence, dispatches bot work or increments admission attempts.
A source whose schedule is no longer Started or whose fingerprint changed is
suppressed. Cursor backoff prevents permanently denied rows starving later rows.

The native after-commit callback sends web fanout after persistence. Failed web
fanout remains in the existing UserNotification pending queue for its recovery
worker. Delayed delivery rechecks current account, project owner, card visibility,
column, and web subscriptions before any socket publication. Persistence is exactly once per occurrence; network delivery retains
the native pipeline's delivery semantics. No claim of exactly-once socket
transport is made. Existing dispatch leases that expire become uncertain and
are notified without replaying the bot request.

## Verification scope

Tests use real SQLite and disposable PostgreSQL 17 storage, native scheduler
HTTP transport with mocked provider responses, native authorization and
unsubscriptions, concurrent recovery, rollback after insertion, terminal
recovery without work replay, and deletion of the visible notification. UI
uses the existing notification renderer and type/filter maps with English,
Korean, Japanese and Chinese labels. These are source tests; authenticated UI,
provider delivery, deployed behavior and business acceptance remain unverified.
