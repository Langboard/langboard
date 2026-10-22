# Fresh installation and database upgrades

Langboard migrations support SQLite and PostgreSQL. Use the migration entrypoint
shipped with the selected application version before starting that version's
producers and workers. Do not replace migration execution with schema creation or
revision stamping.

PostgreSQL uses its native enum and JSONB representation; SQLite uses the supported
portable representations. Migration inspection must use the selected database's
actual schema rather than assuming PostgreSQL catalog tables are available.

An empty-database installation and an upgrade of retained application data are
different operations. Back up a populated database before upgrading, preserve its
migration lineage, and check compatibility with the previous application version
before planning rollback. Corrections to historical migrations do not automatically
alter a database whose corresponding revisions are already recorded as applied.

Do not assume that downgrade is supported for every historical revision. Migrations
that retain pending work or ownership data can refuse destructive downgrade. Keep
those records and resolve the documented compatibility condition before rollback.
