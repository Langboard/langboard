# Markdown source editing

`MarkdownSourcePlugin` is an application-owned Plate plugin, registered through
`EditorKit`. It uses public plugin options/API, Slate operations and DOM/selection
conversion APIs. Plate packages and their source are not patched. `MarkdownKit`
preserves exact source while the live document matches the recorded snapshot;
explicit fragment exports and externally changed documents use the normal serializer.

Card descriptions expose a toggle on desktop
(viewport at least 1024 CSS pixels, hover and fine pointer). It defaults on. The
last mode and pane order are stored per authenticated user in this browser and propagated to
other tabs; it does not claim cross-device synchronization. Mobile does not change
that choice. Comments, read-only cards and other editor types do not expose the mode.

The source pane defaults to the left. Swap panes reverses the layout. Transitions
use 120–140 ms Web Animations and respect reduced-motion preferences.

The existing Plate editor remains the live preview and collaboration document.
Original-source positions are measured before escape preprocessing. Parsed text
positions map to Slate paths and DOM ranges. Source changes apply text and tree
deltas instead of resetting the Yjs document. Existing save, draft and conflict
handlers continue to own persistence. Failed parsing preserves the source draft.

## TOAST reference and attribution

Behavior was reviewed against `nhn/tui.editor` commit
`0c5c11bac0b9954e2cac2ff2a081ce8c80a5f14d`, particularly:

- `markdown/scroll/{scrollSync,dom,offset,animation}.ts`: node-based scroll anchors,
  scroll event feedback suppression and keeping the active block visible.
- `markdown/plugins/previewHighlight.ts` and `markdown/mdPreview.ts`: linking the
  source caret to preview nodes; changing only the active highlight.
- `convertors/convertor.ts` and `editorCore.ts`: selection continuity across modes.
- `__test__/unit/markdown/keymap.spec.ts`: list, task and quote continuation, empty
  marker termination and selected-line indentation.

This implementation translates those behaviors to Plate and native browser APIs.
It does not import ToastMark or ProseMirror. Source-derived adaptations retain the
MIT notice in `TOAST-LICENSE.txt`.

## Verification

`node --experimental-strip-types --test src/components/Editor/plugins/markdown-source/source-model.test.ts`
checks source/path mapping and keyboard behavior. The normal Quality Gate checks
types, lint and production build. Browser acceptance must additionally exercise
Korean IME, both scrolling directions with wrapped text and delayed images,
preview click/caret behavior, mode/reload/user isolation, drafts/CAS and two-tab
collaboration. Source or unit checks alone do not establish canary acceptance.
