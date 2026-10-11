# Provider extraction boundaries

Source audit: 2026-10-11. Independent repositories currently provide panels,
optional declarations and selected archive adapters. They do not yet replace
the host's provider onboarding and ingestion paths. No legacy path can be removed
on the strength of repository separation or unit tests alone.

## Current provider execution map

All paths below are relative to `src/api/langboard`.

| Provider | Current host entry points | Provider-specific work to move | Host responsibilities to retain |
| --- | --- | --- | --- |
| GitHub | `routes/board/BoardGitHubAppApi.py`; `apps/GitHubManifest.py`, `GitHubAuthorization.py`, `GitHubInstallation.py`, `GitHubConnections.py` | App manifest conversion, OAuth provider exchange, app JWT/installation API protocol, repository inspection | Authenticated actor and callback binding, current policy, connection ownership, reviewed trust, SecretRef access and revision fences |
| GitHub | `apps/GitHubResources.py`, `GitHubLifecycle.py`, `GitHubSignal.py`, `GitHubSignalWorker.py` | Repository/lifecycle/check payload parsing, provider identities and outcome normalization | Selected resource authority, immutable import/delivery identity, native signal persistence and current revocation checks |
| GitHub | `apps/GitHubHealth.py`, `GitHubHealthWorker.py`, `GitHubHealthTask.py`; `commands/RunBrokerCommand.py` | Provider installation health refresh and fanout | Existing receipt/queue recovery during migration; never infer provider health from transport ACK |
| GlitchTip | `routes/board/BoardGlitchTipAppApi.py`; `apps/GlitchTipConnection.py`, `GlitchTipSignal.py` | Instance API discovery, organization/project/issue protocol, cursor handling, issue normalization | Connection/resource consent, ownership and SecretRef fences; bounded inbox storage |
| Dokploy | `routes/board/BoardDokployAppApi.py`; `apps/DokployConnection.py`, `DokploySignal.py` | Project/environment/application/compose discovery, deployment API reads and normalization | Resource selection and current access revision, connection ownership and native evidence |
| Dokploy | `apps/DokployWebhook.py`, `DokployNotificationConfig.py` | Notification payload format, provider callback configuration verification | Receiver authentication/configuration revisions and bounded receipts until a proven external replacement exists |

`GitHubLifecycle` schedules health recovery; `GitHubSignalWorker` schedules signal
draining through `GitHubHealthTask`. `RunBrokerCommand` imports those task
registrations. The dedicated board routes call these modules directly. Moving
normalizers alone therefore does not remove host execution or credential access.

## Existing common seam

`routes/settings/AppSignalApi.py` accepts signed, bounded standard envelopes at
`POST /apps/v1/boards/{board}/signals`. Shared `AppSignalIngestion` owns current
credential, board, definition, binding and selected-resource checks, finite
event/outcome policy, revision conflicts and duplicate identity. SDK `AppSignals`
signs exact bytes and does not retry an ambiguous publication automatically.
The external app must normalize provider bodies before using this seam.

`AppSignalProjection` and `SignalInbox` own native evidence visibility and inbox
pagination. `AppCardCreation`, `AppCardScope`, `AppCardInteractions` and
`AppCardArchive` remain separate host contracts. Publishing a signal does not
create or own a card, approve a request, change workflow or authorize execution.
Provider credentials stay in the server-side app, never its browser panel.

## Migration gates

1. Register and approve the external app's finite resource/event policy, create
   its explicit connection and obtain board/resource consent. Keep old keys,
   connections and histories intact; no implicit credential transfer.
2. Run the external provider reader/normalizer and publish through the common
   signed API. Compare explicit resource identity, occurrence versus observation
   semantics, reversed/duplicate delivery and current revocation behavior.
3. Exercise real provider reads and authenticated native inbox/card surfaces.
   Retain a compatible legacy route while replacement acceptance is incomplete.
4. Connect optional writes only with separate consent, current provider
   permission, required human response and independently reported readback.
   Archive retention must never become an implicit deployment or PR merge.
5. Verify independent deployment and rollback with retained receipts and revoked
   authority denied, then retire the corresponding provider host path.

Open work: all three complete reader/normalizer services, onboarding callback
migration, logical app/component identity, existing key compatibility, deployed
browser acceptance and real provider readback. This audit is an inventory and
boundary decision, not extraction completion.
