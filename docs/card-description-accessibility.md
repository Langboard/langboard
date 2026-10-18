# Card descriptions and attachments

## Reading and copying descriptions

Use the card's content panel to scroll a long description. The panel can receive
keyboard focus. Section markers offer navigation within the description without
opening the editor.

The explicit Copy button copies the stored Markdown description, including
sections outside the visible viewport. Browser clipboard permission can prevent
copying; a copy error does not change the saved description. Native text selection
can be used to copy a visible passage.

Long descriptions render sections as they enter the viewport. Keyboard focus and
an accessible name do not establish that every offscreen section is
available to every screen reader. Open the editor to access the complete body;
use Cancel to leave without saving changes.

## Attachments and upload failures

Select a file from the card's attachment action. A successful upload adds the
saved attachment to the card; reload the card to retrieve its persisted list.
File selection alone does not confirm upload. Check that the named attachment
appears before treating the upload as complete.

A failed upload clears its busy state and can be removed from the upload list.
An explicit retry starts a new attempt; uploads are not automatically retried.
A browser transport error may prevent the request from reaching the server.
Read the card's attachment list before retrying an upload with an unknown outcome.
Server authorization and file-size limits still apply.

MCP users can retrieve attachment metadata through `get_card_bundle` with
`include=["attachments"]`. Attachment content reads require current access to
the parent card and accept at most 8 MiB per attachment. See
[Card MCP tools](card-mcp-canonical-tools.md) for permissions and discovery.

## Language and app connection feedback

The editor task-list control and model-download failure notifications use the
active interface language. A translated error does not establish its underlying
cause; provider diagnostics can contain additional details.

Built-in app providers can offer their own connection setup form. An unknown
provider uses the generic fallback. An available setup form does not confirm a
connected provider, consent or completed workflow. Follow the
[external app integration guide](external-app-sdk.md) for connection lifecycle
and authorization requirements.
