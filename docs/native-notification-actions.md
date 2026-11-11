# Native notification read scope

`mark_notifications_read` marks an explicitly selected notification scope as read.
`change.scope=one` requires a nonblank `notification_uid`;
`change.scope=all` forbids it. Extra fields and missing scopes are rejected before
any command runs. Querying unread notifications does not mark them read.

The facade is user-only and dispatches through canonical authorized commands.
One-notification ownership and all-notifications receiver filtering remain in
the existing domain service. The all scope marks only visible unread notifications;
hidden notifications and another receiver's notifications remain unchanged. It returns the native typed `{ "read": true }`
contract. Output validation follows execution and must not imply rollback.

Only invoke this mutation for the scope explicitly requested by the user.
Existing primitive names remain available. See [native MCP profiles](native-mcp-profiles.md)
and [native MCP OAuth](native-mcp-oauth.md) for transport and authorization requirements.


## Personal card activity subscriptions

The card notification popover offers `ignore`, `comments`, and
`comments_and_moves`. The last mode includes both comment additions and card
moves; editing a comment or reordering within the same column does not generate
an activity notification. A card has no ordinary activity subscription until its
viewer explicitly chooses a mode. Mentions and assignment settings remain
separate, and existing Web-channel unsubscribes still take precedence.

Authenticated users can read and update their own mode at
`/notification/settings/project/{project_uid}/card/{card_uid}/activity` using GET
and PUT (`{"mode":"comments"}`). Current card visibility and project membership
are checked before either operation. A preference never grants card access and
does not change the card's revision or unread cursor.

Notifications use the existing durable notification and after-commit delivery
pipeline. Delivery rechecks current visibility, account activation and the saved
mode, including recovered pending delivery. Same-identity board moves recheck
access in the destination board and omit the source column name. Self actions,
replayed comment requests and rolled-back writes do not add duplicate activity
notifications. Ordinary activity notifications use the Web channel; existing
mention/assignment email templates are unchanged.
