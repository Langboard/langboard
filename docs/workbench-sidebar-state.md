# Workbench sidebar state

The shared dashboard/board shell owns Explorer collapse state. Page visibility
and mobile drawers remain separate from desktop collapse state.

## Preference and responsive defaults

- Explicit collapse/expand and completed resize gestures write the current user's
  `workbench_sidebar_collapsed` value through the existing user-settings store.
- Automatic collapse at 1280px or less is a responsive default, not a saved user
  choice. It must not overwrite an explicit preference during mount or reload.
- A saved `false` means explicitly expanded; it must not be treated as missing.
- Account changes select the new user's value. Unset accounts use the responsive
  default rather than another account's explicit choice.
- The generic `ResizableSidebar` accepts controlled state and reports whether a
  change came from the user, responsive sizing or restoration. Other consumers
  can retain its existing uncontrolled default behavior.

Preferences use the existing browser-local user-settings persistence. This does
not introduce cross-device synchronization or another settings service.

## Regression evidence

The real shared-shell fixture reproduces both reload failures and account state
leakage before the fix. New tests cover collapsed and expanded states across
board navigation and reload at 1440px and 1100px, plus switching between two
users. Existing mobile focus/Escape, rail and sidebar checks remain in the
workbench Playwright profile. Test dependencies must resolve the current
`@langboard/core` build, not another checkout's stale package output.

Source checks do not constitute deployed acceptance. Verify both states after
navigation and reload on the actual canary before closing the product card.
