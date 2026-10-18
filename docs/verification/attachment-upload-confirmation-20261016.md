# Attachment upload confirmation

## Native reproduction

On deployed canary API `0e14576b60f476e0e16b3ace6e5ef184e496cf24`, a staff
operator used Chrome and the actual macOS Open panel to select a synthetic
68-byte UTF-8 text file on the existing owned QA card. The browser POST returned
201 with the persisted attachment. Independent MCP readback found that same
attachment. Reloading the card displayed it, while the original long-lived tab
had not added it to the attachment list immediately after the successful POST.
This proves native staff upload and persistence, not an external-account upload
or the cause of a missing realtime event.

## Shared client boundary

The upload mutation returned the complete saved attachment but left local model
hydration entirely to the realtime event handler. Hydrate the existing
ProjectCardAttachment registry directly from the successful HTTP response, as
other native mutation hooks already do. Keep the existing realtime handler for
other clients. The registry uses the attachment UID to update an existing model,
so a later push updates the same attachment instead of creating another list row.
No optimistic attachment, extra request, polling, new store, or dependency is
introduced. Failed uploads do not reach hydration; automatic POST retry stays off.

## Evidence boundary

The deployed reproduction predates this fix. Local focused lint and source
inspection do not establish deployed UI acceptance. After the next verified UI
release, repeat one native upload and verify the list updates before reload,
then verify persistence after reload. Preserve external create-only authority
and app-workflow acceptance as separate gates.

## App settings feedback discovered during native acceptance

The deployed connection page also rendered the built-in provider setup form
alongside a message claiming connection setup was unavailable. The message was
controlled by panel/inbound metadata, not the actual setup form. It was not
proof of a server policy denial.

A single module-level map now selects each existing built-in connection form
and controls that fallback message. This removes three repeated render branches,
retains each form across section changes, and leaves unknown providers with the
existing generic fallback. It changes no connection, consent or policy. Native
post-deployment verification is still required; rendering an available form does
not establish a connected provider or completed signal/HITL/receipt acceptance.

## External permission and persistence checkpoint

API authority `7ada1de632d3a2a2ae661e0c1982af2487e8a380` is deployed.
A separate non-admin external fixture with board read/create/update permissions
created a new card and saved a Korean description through normal browser UI.
Reload and independent retrieval confirmed the persisted card and body. An
unchanged read/update-only external fixture received HTTP403 on create, and
subsequent listing confirmed no card was created.

The external browser file chooser selected the synthetic file, but Chrome
blocked the subsequent upload request with `net::ERR_ACCESS_DENIED`; no server
attachment was created. A separate normal external-user HTTP upload returned201,
and a native browser reload displayed that named attachment. These are separate
proofs: the fallback establishes server upload/persistence and browser retrieval,
not successful native file upload or immediate post-upload list confirmation.

Realtime acceptance remains open. API-bound authorization/readiness requests
intermittently exceed the realtime service's existing3-second response budget,
producing503 responses and reconnects under observed scheduling pressure.
Neither a credential mismatch nor a client protocol defect is established by
that observation. Preserve the failed attempt and diagnose the request path
before claiming stable live updates.

The UI follow-up now includes the deployed API authority without changing its
API, shared, Graph, socket or Compose files. The two-line generic Graph Compose
configuration is preserved separately in draft PR#705, so this UI release can
retain the existing complete Compose identity contract. That separate change
requires its own Graph product and runtime acceptance.

## External checkpoint and failed-upload recovery, 2026-10-18

A new non-admin external fixture was created through normal account and board
membership APIs. Existing members and the pending invitation were retained.
Normal sign-in, shared-card creation, details update, comment creation, and a
synthetic text attachment succeeded. A fresh external session retrieved the
saved content and downloaded the exact bytes. The browser displayed that same
card, comment and attachment; access to an unrelated internal board returned403.
These checks do not establish all governance or collaborative-editor behavior.

The subsequent synthetic PNG browser upload failed locally with
`net::ERR_ACCESS_DENIED`, without an HTTP response. Independent attachment
readback confirmed the PNG was absent before one explicitly instrumented retry.
That retry failed the same way. Native file-picker comparison remains pending
because the workstation is locked. No server upload failure or authentication
cause is inferred from the browser transport error.

The failure exposed two independent UI defects: the upload callback never
cleared its uploading state on rejection, and the batch message attributed every
failure to the file-size limit. The callback now always clears busy state and
resets old error/progress state on an explicit retry. A failed row remains
removable with an accessible, translated Delete button and displays a generic
failure. The batch message has English, Korean, Japanese and Chinese resources.
Unknown progress totals stay finite. Existing authorization, server limits,
no-automatic-retry policy, and partial-success removal are unchanged.

The actual component fixture exercises an aborted upload at desktop1440px and
mobile390px, verifies a removable failure, exactly one POST and no horizontal
overflow. The original component fails the same fixture; both corrected cases
pass. This is source regression evidence, not a new deployed-UI acceptance.

## External browser editing checkpoint, 2026-10-18

On the same external fixture, the actual card editor accepted a unique Korean
body marker and completed explicit Save. Reloading the card displayed the marker;
an independently authenticated HTTP session retrieved it and the unchanged
attachment bytes. This is browser body-save evidence on the existing release,
not acceptance of the pending editor-readiness or upload UI changes.

Editing the existing owned comment then exposed a remaining deployed regression.
The desktop card rendered the same comment editor in both the body and side
panel. After synchronization and one explicit Save, fresh HTTP retrieval contained
the original text twice and the unique edit marker once. Reload displayed the
same persisted result in two comment surfaces. Do not classify this as successful
comment-content acceptance merely because the update returned successfully.

The pending single-surface rendering change already excludes inline comments
when the side panel owns them and excludes the panel list when it is closed.
It must be checked on the new product image with the exact edit/save/reload flow.
The fixture is disposable QA data; customer-owned content was not edited.
