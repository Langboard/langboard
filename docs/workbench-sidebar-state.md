# Workbench sidebar preferences

Explorer collapse preferences apply to the shared dashboard and board shell.
Explicit collapse/expand actions and completed resize gestures save the current
user's choice in browser-local settings. The choice persists during board
navigation and reload in that browser; it does not synchronize across devices.

Without a saved choice, the sidebar automatically collapses at viewport widths
of 1280px or less. Automatic responsive changes do not overwrite an explicit
collapsed or expanded preference. Switching accounts selects the new account's
saved value; an account without a preference uses the responsive default.
Mobile drawers have separate behavior from the desktop collapse preference.

## Card state summaries

Board cards can show a single state summary below the title when state data is
available. Completion, blockers and execution status remain separate. Unchecked
items in a completed workflow are diagnostic; checklist completion does not grant
approval.

Known state reasons have English, Korean, Japanese and Chinese translations.
Unknown reasons use a neutral fallback. The summary has a screen-reader label
and a full hover title. Raw reason messages and source references are not shown,
so the summary does not disclose the title or identity of an inaccessible
prerequisite.
