"""Official LangGraph PostgreSQL storage; one dimension-bound schema per model fingerprint."""

import re
from contextlib import contextmanager
from math import ceil


@contextmanager
def open_postgres_vector_store(embeddings, fingerprint: str, *, dimensions: int, timeout_seconds=10.0, create=True):
    from langgraph.store.postgres import PostgresStore
    from psycopg import Connection, Error, sql
    from psycopg.rows import dict_row
    from sqlalchemy.engine import make_url
    from ...Env import Env
    from .DocumentEmbedding import validated_embeddings
    from .DocumentLangGraphVectorStore import DocumentLangGraphVectorStore

    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid embedding fingerprint")
    if type(dimensions) is not int or not 1 <= dimensions <= 16000:
        raise ValueError("Invalid PostgreSQL vector dimensions")
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 30:
        raise ValueError("Invalid PostgreSQL vector timeout")
    if create and embeddings is None:
        raise ValueError("Index creation requires configured embeddings")
    url = make_url(Env.MAIN_DATABASE_URL)
    if url.get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL retrieval requires a PostgreSQL main database")
    # Core database identity is the instance boundary. Full fingerprints remain
    # in every source filter; schema identifiers stay below PostgreSQL's 63-byte limit.
    schema = "lbv_" + fingerprint[:58]
    try:
        with Connection.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            connect_timeout=ceil(timeout_seconds),
            options=f"-csearch_path={schema},public -cstatement_timeout={ceil(timeout_seconds * 1000)}",
        ) as connection:
            if (
                not create
                and connection.execute("SELECT to_regclass(%s) AS relation", (schema + ".store",)).fetchone()[
                    "relation"
                ]
                is None
            ):
                yield None
                return
            extension = connection.execute(
                "SELECT n.nspname AS schema FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
                "WHERE e.extname='vector'"
            ).fetchone()
            if not extension or extension["schema"] != "public":
                raise ValueError("Install pgvector in the public schema before enabling PostgreSQL retrieval")
            if create:
                connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            store = PostgresStore(
                connection,
                index={
                    "dims": dimensions,
                    "embed": validated_embeddings(embeddings, dimensions),
                    "fields": ["text"],
                    "distance_type": "cosine",
                    "ann_index_config": {"kind": "flat"},
                }
                if embeddings is not None
                else None,
            )
            if create:
                # Official migrations only on explicit indexing, never during search or cleanup.
                store.setup()
            if embeddings is not None:
                vector_type = connection.execute(
                    "SELECT format_type(a.atttypid,a.atttypmod) AS type FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relname='store_vectors' AND a.attname='embedding' AND NOT a.attisdropped",
                    (schema,),
                ).fetchone()
                if not vector_type or vector_type["type"] != f"vector({dimensions})":
                    raise ValueError("Stored vector dimensions do not match the embedding configuration")
            yield DocumentLangGraphVectorStore(store)
    except Error:
        raise RuntimeError(
            "PostgreSQL document storage unavailable; verify database permissions and connectivity"
        ) from None
