# Identifiers and collision handling

Langboard stores identifiers as signed 63-bit integers in bigint fields and
exposes resource UIDs as 11-character short codes. Keep UIDs as strings in API
clients and pass the returned value unchanged when referring to a resource.
Existing integer IDs and short-code round trips remain compatible.

Identifier allocation across processes and replicas relies on database
primary-key enforcement. On PostgreSQL and SQLite, an ID collision triggers a
bounded allocation retry without overwriting the existing row. Other unique
constraint violations remain errors.

If allocation is exhausted, the operation fails with no new row inserted for
that allocation; the existing row remains unchanged. A batch failure is not a
successful partial-write receipt: callers must use the operation's transaction
and outcome semantics before deciding whether to retry.
