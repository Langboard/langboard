# Card titles and inferred labels

Card creation and title updates preserve the supplied title, including leading
`[Bug]`, `[버그]`, `[개선]` or registered alias tokens. Normal field validation and
outer whitespace rules still apply. The same native service owns UI, REST and
MCP writes; labels never authorize a title rewrite.

Consecutive leading tokens can additionally attach exact registered global
labels. Original names, registered translations and aliases share one lookup.
Unknown or ambiguous tokens remain literal, and matching labels are assigned
at most once without replacing existing local labels. A title consisting only
of matching tokens retains the existing no-inference behavior.

Registry changes and assignment failures preserve the requested title and use
the existing transaction/savepoint recovery. Existing stored titles and labels
are not renamed or migrated. In particular, text removed by an earlier version
cannot be reconstructed safely from a label and is not fabricated.
