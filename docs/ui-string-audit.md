# User-facing string regression checks

The existing TypeScript AST scanner checks source literals in rendered JSX,
accessible attributes, known creation defaults, dialogs, toast messages and
validation messages. It is a bounded static check, not full data-flow analysis
or proof that every language surface is translated.

## Custom props and computed notifications

The 2026-10-18 regression showed that custom `tooltip` and `label` props and
conditional/template toast or validation messages were omitted. Reuse the
scanner's existing rendered-expression traversal for these cases. Translation
calls and user/model expressions remain excluded; source strings are reported,
never rewritten automatically. The regression includes user-owned label, body
and name-template values to prevent treating dynamic content as translation keys.

The full scan exposed two actual untranslated surfaces: the editor task-list
button's `Todo` tooltip and Ollama model-pull failure fallback. The tooltip now
uses the existing four-language `editor.Todo` key. Both live and reconciliation
failure notifications use one four-language message. Backend diagnostics remain
in their existing API/event payloads; raw backend text is no longer the toast.
The callback reads the active i18n instance at invocation so an existing polling
closure does not retain a translated sentence from a previous language.

`PR` is retained as a technical pull-request abbreviation and recorded as one
explicit baseline exception. No generated blanket baseline refresh was used.
The previous20 exceptions remain; the new scanner reports21 existing technical
candidates and zero new candidates. The original missed-case test failed before
the scanner change; all six scanner regressions pass afterward. Four-language
resources contain30 namespaces/2,653 values with matching structure and
interpolation tokens. Application/tooling TypeScript, targeted product ESLint,
Prettier and diff checks pass.

These are source/static validation results. Actual editor tooltip interaction,
Ollama failure delivery and runtime language changes on the newly deployed
canary remain separate acceptance gates. The full UI-string migration card is
not complete or archived on this evidence.
