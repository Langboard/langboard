# Panel resource selection (unreleased proposal)

This proposal adds a panel request for opening the host's human resource-selection
form. Released SDK 0.2.7 artifacts do not support it. Use the operation only after
a compatible SDK and host release are available; a package version alone does not
establish host support or permission.

```js
if (session.supportedOperations?.includes("resources.open")) {
  await session.request("resources.open", {
    candidates: [
      {
        resource_type: "workflow",
        external_resource_id: "erp:workflow:example",
        label: "Example workflow",
      },
    ],
  });
}
```

The request accepts 1–20 suggestions. Each has exactly these string fields:

| Field                  | Maximum length | Meaning                                                                        |
| ---------------------- | -------------- | ------------------------------------------------------------------------------ |
| `resource_type`        | 64 characters  | A resource type supported by the application                                   |
| `external_resource_id` | 200 characters | An identifier obtained through the application's own authorized selection flow |
| `label`                | 160 characters | Display text, rendered as plain text                                           |

Empty values, surrounding whitespace, control characters, duplicate resource
identities and additional fields are rejected. The SDK's existing JSON/UTF-8
transport limit also applies. Do not provide a user, board, connection, credential,
grant, selected state or revision as authority.

The person chooses an existing connection they can manage, then confirms each
resource change in the host. The host reads current authorization and revisions
before saving. Cancel writes nothing. A stale or unobserved result requires a
fresh review; it is not automatically resent. Multiple resources require separate
confirmations and do not form an atomic bulk grant.

An `{ state: "opened" }` response means only that the form opened. It is not a
saved selection, consent receipt or permission to execute a workflow. Read
`resources.list` again to observe approved resources. Closing the panel, changing
its identity or board, revoking authority or aborting the session discards the
suggestions.

An empty approved-resource list is valid for initial selection. The application
must obtain its proposed identifiers through its own authorized user flow; the
SDK does not discover business entities, infer identifiers from existing resources
or provide an application login or credential bridge. Connection creation remains
part of the existing host onboarding flow.

The native host imports its bridge from
`@langboard/host-runtime` and advertises `resources.open` only when native inbound
resource management is implemented. This advertisement is transport availability,
not authority. Older clients retain the five legacy operations. The host exposes
the installed client and runtime module identities separately in its resource
manifest.
