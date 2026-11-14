"""Native wiki replacement primitive with retained immutable object provenance."""

from sqlalchemy import Text, cast, func, literal_column
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from ....core.db import SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.types import SafeDateTime
from ...models import ProjectWikiAttachment


def wiki_file_identity(file):
    column = ProjectWikiAttachment.column("file")
    dialect = DbEngine.get_main_engine().dialect.name
    if dialect == "postgresql":
        decoded = cast(cast(column, JSONB).op("#>>")(cast([], ARRAY(Text))), JSONB)

        def value(key):
            return decoded.op("->>")(literal_column(f"'{key}'"))
    elif dialect == "sqlite":

        def value(key):
            return func.json_extract(func.json_extract(column, literal_column("'$'")), literal_column(f"'$.{key}'"))
    elif dialect in {"mysql", "mariadb"}:

        def value(key):
            return func.json_unquote(func.json_extract(func.json_unquote(column), f"$.{key}"))
    else:
        raise ValueError("Unsupported database dialect for file provenance")
    return tuple(value(key) == getattr(file, key) for key in ("storage_type", "storage_name", "filename"))


def replace_attachment(db, documents, target, filename, content_hash, stored, *, same_content):
    """Caller holds current wiki/attachment authority and confirmation revision locks."""
    owners = db.exec(
        SqlBuilder.select.table(ProjectWikiAttachment, with_deleted=True)
        .where(*wiki_file_identity(target.file))
        .limit(2)
    ).all()
    if len(owners) != 1 or owners[0].id != target.id or target.replaced_by_id is not None:
        raise ValueError("Attachment provenance is missing or ambiguous")
    if not same_content:
        db.insert(
            ProjectWikiAttachment(
                project_wiki_id=target.project_wiki_id,
                user_id=target.user_id,
                filename=target.filename,
                file=target.file,
                content_sha256=target.content_sha256,
                order=target.order,
                replaced_by_id=target.id,
                deleted_at=SafeDateTime.now(),
            )
        )
        target.file = stored
    target.filename = filename
    target.content_sha256 = content_hash
    db.update(target)
    if not same_content:
        documents.supersede(db, target)
    return target
