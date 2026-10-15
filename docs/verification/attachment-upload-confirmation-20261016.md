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
