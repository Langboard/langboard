# Card description accessibility acceptance

The card content viewport is a keyboard focus stop and a region named by the
current card title. It uses the existing model subscription so a saved title
change updates its accessible name. This makes the actual scroll container
reachable independently of buttons and links inside it. Browser-native scrolling,
the existing description rail, virtualization, editor, and copy behavior remain
in place. No new component, state store, or runtime dependency is required.

The description-scroll fixture already provided `tabIndex={0}`; the real card
viewport did not. Passing the isolated fixture was therefore insufficient proof
that a keyboard user could focus the actual viewport. The production viewport now
provides explicit focus and naming too. Scoped ESLint and the full UI typecheck
pass; these checks do not establish browser or screen-reader acceptance.

## Required runtime acceptance

Use a disposable card with a long Markdown description containing headings,
multilingual paragraphs, a table, a link, and a recognizable final paragraph.
At desktop and 390-pixel mobile widths:

- Reach the named card region with Tab, then navigate with PageUp/PageDown and
  Home/End. Confirm the final paragraph and intermediate table/link are reachable.
- Navigate description rail markers by keyboard without entering edit mode.
- Activate the explicit copy button and compare the clipboard with the original
  stored Markdown, including content outside the rendered virtual window.
- Verify native partial selection copy without intercepting Cmd/Ctrl+A or copy.
- Edit, cancel, edit/save, and reload. Confirm unchanged sections and the final
  paragraph persist, including table and link structure.
- Use a screen reader to check heading, paragraph, table, and link order, including
  virtualized offscreen sections. Keyboard focus and accessible names alone do
  not prove that all content is available to a screen reader.

## Current acceptance status, 2026-10-17

Authenticated canary testing created a disposable long-description card, but
initial board/card reads and explicit card retry failed before content rendered.
The observed XHRs were canceled after 46–509 ms; identity reads returned 200 and
the preflight for the card retry returned 200. No 403 or 15-second timeout was
observed. Several unrelated reads were canceled too. These observations do not
establish a renderer, permission, or backend root cause.

Runtime acceptance remains incomplete. The source focus correction is unshipped.
Keep the work card active until the deployed runtime passes the checks above.

### Subsequent canary observation, 08:11 UTC

An authenticated card retry returned HTTP 200 after approximately 1.19 seconds
and rendered the long QA description. The final rail marker revealed the final
sentinel. Entering edit mode exposed all 18 sections, tables, links, and the
sentinel; cancel restored read mode without a save. The deployed content viewport
still had `tabIndex=-1`, no region role, and no accessible name, confirming that
the source focus correction was not deployed.

The explicit copy action showed its success toast, but two reads through the
browser automation clipboard returned an empty string. This is an unresolved
observation, not proof that the stored Markdown was copied or that the application
clipboard implementation is defective. No content was pasted or saved. A saved
edit/reload, native partial selection, mobile, and screen-reader acceptance remain
outstanding. The board was still in its load-error state when the card rendered;
comments, seen state, and column history requests were canceled after about
1.06 seconds. No shared root cause has been established.
