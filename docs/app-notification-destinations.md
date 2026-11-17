# Notification destinations, consent and delivery

This document describes the implemented personal test-notification and receipt contracts. Source implementation, deployed revision and authenticated provider acceptance are distinct. Card activity delivery, selected shared destinations/deadlines and their change/cancel/outage-summary flows remain incomplete. Do not derive a second wire format from an earlier proposal.

Ownership: Langboard owns native user approval, proof state, subscription generation, event snapshots and receipts. A generic receiving app owns verified external login, opaque destination resolution, its existing durable outbox and provider reconciliation. An app credential is not a native user delegation.

Personal destination proof
--------------------------

The signed-in native user starts
`POST /settings/apps/connections/{connection_uid}/notification-destination-challenges`
with `request_id`, the current 64-character `expected_connection_revision`, and
`kind:"personal"`. The initial response includes a ten-minute `challenge_token`
and challenge metadata. Only the nonce hash is persisted; replay does not recover
the token. A receiving app credential cannot create this user consent.

The receiving app uses
`POST /apps/v1/notification-destination-proofs` with its current connection bearer
and `challenge_uid`, `challenge_token`, `request_id`,
`external_identity:{issuer,subject}` and
`destination:{kind:"personal",external_id,display_name}`. The app derives identity
and the opaque destination from its verified external login/binding. The response
contains `proof_uid`, `challenge_uid`, `revision` and
`state:"awaiting_user_approval"`; this does not grant a subscription or send.

The initiating native user reads
`GET /settings/apps/notification-destination-challenges/{challenge_uid}` and
explicitly submits
`POST /settings/apps/notification-destination-challenges/{challenge_uid}/approve`
with `expected_revision` and `proof_uid`. The destination response contains
`destination_uid`, `connection_uid`, `app_key`, `kind`, `revision` and `state`.
Cancellation uses the challenge `/cancel` route with `expected_revision`; an
approved destination uses
`POST /settings/apps/notification-destinations/{destination_uid}/revoke` with its
current `expected_revision`. Current user, connection, proof and policy checks
apply to each operation. Matching emails or existing room read access are not
identity proof or permission to send. Shared-room approval is not implemented by
this personal flow.

App-wide approved service targets
--------------------------------

An instance administrator binds one enabled app service template to an existing
HTTPS webhook and its secret reference. The target must pass the same declaration
trust and event allowlist checks as an explicit connection destination. No
webhook, secret or signing key is copied per user.

A connection owner reads its service template metadata, including only the
service origin, template revision and the connection revision already returned
by native connection discovery. Preparing that approved service destination pins
the exact template revision on that connection; it cannot accept a callback URL
or overwrite an explicit administrator connection binding. Preparing a target
is configuration only and never issues a credential or approves custody.

Credential custody remains a separate explicit user approval. A changed or
disabled template fences old connection pins and credentials; refreshing a pin
does not silently reapprove custody or retarget an already accepted event.
The user reviews and approves the new destination revision before another
handoff. No per-user administrator click is required for an unchanged approved
service template.

The following personal test vertical is implemented. Complete onboarding UI,
card activity subscriptions, shared delivery and live provider acceptance remain
separate requirements. Configuration alone does not establish those results.


Personal test-notification wire contract
---------------------------------------------------------

The user-authenticated POST /settings/apps/notification-subscriptions accepts
scope=personal, an already explicitly approved destination_uid, its exact
expected_destination_revision and a stable request_id. Subscription approval is
separate from destination proof and credential custody. This first vertical
accepts only a personal test notification; it does not subscribe to card events
or share card content with rooms. Those broader flows remain unimplemented.

POST /settings/apps/notification-subscriptions/{subscription_uid}/test takes
expected_generation, request_id, title and body. The native host persists one
immutable notification dispatch and one existing AppExecutionOutbox event.
Replaying identical admission returns that event without requeueing it; changed
content under the same request ID conflicts. The event expires after ten minutes.
Revocation increments the subscription generation and fences retained events.

The signed event type is io.langboard.notification.requested.v1. Its body has
schema_version=1, event_uid, event_type, app_key, connection_uid,
notification={kind:test,title,body}, notification_fence={event_revision:1,
subscription_uid,subscription_generation,destination_uid,destination_revision},
external_destination={kind:personal,external_id}, expires_at and the existing
transport destination snapshot. external_id is the approved opaque binding,
never a caller-supplied provider address. Signature verification uses the exact
native webhook bytes and existing signature headers.

The app bearer POST /apps/v1/notification-events/{event_uid}/delivery-authorization
requires event_revision, subscription_generation and destination_revision. The
response contains authorized=true, event_uid, those three exact fence values
and checked_at. It contains no content or transferable execution permit. Call
it immediately before provider I/O after claiming the existing consumer queue;
never cache it. Current app/connection identity, host user, explicit proof and
subscription, target revisions, immutable payload and expiry are rechecked.
Unknown or terminal provider receipts forbid another send authorization.

POST /apps/v1/notification-events/{event_uid}/receipts accepts the same fence,
stable request_id, status, expected_revision, reconciled and optional
provider_message_id/reason_code. It returns {event_uid,receipt:{fence,status,
provider_message_id,reason_code,revision}}. Accepted/unknown/confirmed/rejected
transitions use the existing receipt rules; unknown requires explicit provider
reconciliation, and confirmation requires a provider message ID. Replays return
retained evidence without a send. A conflicting request ID or stale receipt
revision is rejected. Transport delivery and provider receipt remain separate.

Late observations use this same receipts endpoint. Event expiry or a blocked
transport event does not erase an already observed provider outcome. The current
app/connection credential, active owner and consent, exact subscription/destination
fences, immutable snapshot, receipt CAS and request-ID checks still apply. A
revoked credential, consent or changed fence remains rejected. A new late
adapter_accepted receipt is rejected; replay of an identical retained admission
still returns its original result. Late unknown/confirmed/rejected observations
never renew delivery authorization, change the outbox state or enqueue a send.
Unknown reconciliation still requires reconciled=true and the current receipt
revision. Delivery authorization and outbox claims continue to reject expired or
blocked events, including after successful late reconciliation.

The source and isolated tests do not establish schema rollout, a live external
provider result, card/HITL integration or authenticated browser acceptance.


User-started proof context in the signed custody handoff
------------------------------------------------------

A user first reads GET /settings/apps/connections/{connection_uid}/credential-custody
to review owner-scoped metadata, its current connection revision and any existing
custody revision. This returns no credential, callback secret or external subject.
Destination configuration and explicit custody approval remain separate actions.

After the native user creates a destination challenge, the existing user-authenticated
handoff request optionally accepts notification_challenge_uid and
notification_challenge_token together. Before resolving the approved callback,
before issuance and after callback acknowledgement, the host revalidates the exact
initiating native user, app/connection identity, pending challenge, nonce hash,
expiry and existing custody approval. A challenge can bind at most one handoff.
A request ID cannot switch, add or remove this context on replay.

Only the existing fixed HTTPS service receives the signed callback. It includes
an optional destination_challenge object with challenge_uid, challenge_token,
expires_at and initiated_by_user_uid. The nonce exists only in the one bounded
callback's memory body; the database retains its hash in the existing challenge
and the association ID in the handoff. User responses and retained receipts do
not return the nonce or credential. A callback cancelled or expired before
native completion leaves the staged credential revoked. The native status
usable decision remains mandatory before activating any staged credential.

The receiver verifies the exact signed bytes and its fixed origin/app/connection/
custody/credential context. It may retain an expiring nonce hash bound to that
context, never make arbitrary caller-supplied connection UIDs a credential lookup.
An externally authenticated user must present the same challenge UID and nonce
through a body or exact-origin channel. Do not put them in URLs, queries, logs,
browser storage or general-purpose provider requests. This context permits only
the pending proof step. External issuer/subject come from the receiver's actual
verified login; matching email strings is not an identity binding.

The proof still awaits the initiating native user's review and approve action.
No app invokes that approval on the user's behalf. Neither the challenge nor
this handoff grants a subscription, room sharing or provider execution authority.
Legacy handoffs without this optional context remain valid; they do not become
proof-credential selectors merely because an external user supplies a UID.


Collaboration notification integration boundary
---------------------------------------------

Native card activity currently uses `card_comment_added` and `card_moved`
notification types. These are not yet automatically admitted into the app
notification outbox. A consumer must not accept an invented content kind, actor
or event shape to bridge that missing implementation. The existing
`io.langboard.notification.requested.v1` envelope, signature verification and
receipt fences are the only implemented app notification wire contract here.

Participation-based enrollment, durable subscription reasons and the signed
collaboration content variant must be supplied by the native owner and official
SDK together. Multiple reasons must not duplicate an event for one recipient.
Explicit user ignore/channel choices and current card access must remain
effective, with source/recipient authority rechecked immediately before delivery.
A personal destination proof does not authorize a shared room. Existing shared
deadline, change/cancel/revoke and bounded outage-summary requirements remain.

Current template metadata exposes an approved service origin and revisions. It
does not expose a `management_url` or establish a browser onboarding-return
contract. Do not invent such a field or put challenge nonces into URLs, browser
storage or logs. The user-started signed custody handoff described above is the existing
proof-context transport; a complete verified-login UI still needs acceptance.
