"""Single native configuration path; environment compatibility is explicit and bounded."""

from sqlalchemy import func, or_, select, update
from ...core.db import DbSession, SqlBuilder
from ...core.search.ElasticsearchKeywordSearch import validated_destination
from ...Env import Env
from ...helpers import InfraHelper
from ..models import DocumentKeywordIndex, KeywordSearchConfiguration, KeywordSearchConnection, SecretReference, User
from .factory.SecretReferenceService import SecretReferenceConflict, SecretReferenceUnavailable


def current_configuration(db, *, lock=False):
    statement = SqlBuilder.select.table(KeywordSearchConfiguration).where(
        KeywordSearchConfiguration.singleton == "instance"
    )
    return db.exec(statement.with_for_update() if lock else statement).first()


def require_admin(actor):
    with DbSession.atomic() as db:
        user = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id).with_for_update()).first()
        if user is None or not user.is_admin or user.deleted_at or not user.activated_at:
            raise SecretReferenceUnavailable()
        return user


def configuration(*, operation="read", connection_id=None, lock=False):
    """Read non-secret identity only. Legacy jobs never acquire a native grant."""
    if operation not in {"read", "index", "remove", "preflight"}:
        raise ValueError("Invalid keyword operation")
    with DbSession.use(readonly=False) as db:
        setting = current_configuration(db, lock=lock)
        if connection_id is not None:
            connection = db.exec(
                SqlBuilder.select.table(KeywordSearchConnection)
                .where(KeywordSearchConnection.id == connection_id)
                .with_for_update()
                if lock
                else SqlBuilder.select.table(KeywordSearchConnection).where(KeywordSearchConnection.id == connection_id)
            ).first()
            if connection is None or not connection.active:
                raise ValueError("Original keyword connection is unavailable")
            if operation not in {"remove", "preflight"} and (
                setting is None
                or setting.connection_id != connection.id
                or not (setting.reads_enabled if operation == "read" else setting.indexing_enabled)
            ):
                raise ValueError("Keyword operation is disabled")
            from ...core.security import KeyVault

            reference_statement = SqlBuilder.select.table(SecretReference).where(
                SecretReference.id == InfraHelper.convert_id(connection.secret_ref.rsplit("/", 1)[1])
            )
            reference = db.exec(reference_statement.with_for_update() if lock else reference_statement).first()
            if (
                reference is None
                or reference.scope != "instance"
                or int(reference.scope_id) != 0
                or reference.state != "active"
                or reference.revision != connection.secret_revision
                or reference.provider != KeyVault.provider.name()
            ):
                raise SecretReferenceUnavailable()
            return {
                **validated_destination(connection.endpoint, connection.index),
                "connection_id": int(connection.id),
                "configuration_revision": setting.revision if setting else None,
                "secret_ref": connection.secret_ref,
                "secret_revision": connection.secret_revision,
                "legacy": False,
            }
        if setting is not None:
            if operation == "remove" or not (
                setting.reads_enabled if operation == "read" else setting.indexing_enabled
            ):
                return None
            if setting.connection_id is None:
                return None
            return configuration(operation=operation, connection_id=setting.connection_id)
    enabled = (
        Env.get_from_env(
            "DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED"
            if operation == "read"
            else "DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED",
            "false",
        )
        == "true"
    )
    if not enabled and operation != "remove":
        return None
    return {
        **validated_destination(
            Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", ""),
            Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX", ""),
        ),
        "connection_id": None,
        "legacy": True,
    }


def index_configuration():
    """Validate queue configuration without accessing native credential material."""
    from ...core.search.ElasticsearchKeywordSearch import _connection_options

    try:
        config = configuration(operation="index")
        if config is None:
            raise ValueError("Keyword indexing is unavailable")
        # Native reference authority was checked above; a fixed validation token
        # exercises static options only. It is never stored or sent to a client.
        key = (
            Env.get_from_env("LANGBOARD_ELASTICSEARCH_API_KEY", "") if config["legacy"] else "native-static-validation"
        )
        _connection_options(
            config["endpoint"],
            config["index"],
            key,
            Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS", ""),
            float(Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_WRITE_TIMEOUT", "10")),
        )
        return config
    except (ValueError, TypeError, SecretReferenceUnavailable):
        raise ValueError("Keyword indexing is unavailable") from None


def enabled(operation):
    try:
        return configuration(operation=operation) is not None
    except (ValueError, SecretReferenceUnavailable):
        return False


def runtime_options(config, *, operation):
    """Resolve material at use; callers perform network only after this returns."""
    from ...core.search.ElasticsearchKeywordSearch import _connection_options
    from . import DomainService

    if DbSession.has_active_transaction():
        raise RuntimeError("Keyword runtime resolution cannot run inside a source transaction")
    if config["legacy"]:
        # Explicit original Env destination only, including retained legacy cleanup.
        destination = validated_destination(
            Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", ""),
            Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX", ""),
        )
        if destination != {key: config[key] for key in ("endpoint", "index")}:
            raise ValueError("Original keyword connection is required")
        if operation != "remove":
            with DbSession.use(readonly=False) as db:
                if current_configuration(db) is not None:
                    raise ValueError("Legacy indexing is disabled by native settings")
        key = Env.get_from_env("LANGBOARD_ELASTICSEARCH_API_KEY", "")
    else:
        service = DomainService()
        try:
            key = service.secret_reference.resolve_for_keyword_workload(
                config["connection_id"], operation=operation
            ).get_secret_value()
        finally:
            service.close()
    timeout = float(
        Env.get_from_env(
            "DOCUMENT_KEYWORD_ELASTICSEARCH_TIMEOUT"
            if operation == "read"
            else "DOCUMENT_KEYWORD_ELASTICSEARCH_WRITE_TIMEOUT",
            "1" if operation == "read" else "10",
        )
    )
    if operation == "read" and not 1 <= timeout <= 2:
        raise ValueError("Invalid keyword read timeout")
    return _connection_options(
        config["endpoint"],
        config["index"],
        key,
        Env.get_from_env("DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS", ""),
        timeout,
    )


def save(
    actor, service, *, expected_revision, endpoint, index, secret_ref, secret_revision, reads_enabled, indexing_enabled
):
    destination = validated_destination(endpoint, index) if secret_ref else None
    with DbSession.atomic() as db:
        require_admin(actor)
        setting = current_configuration(db, lock=True)
        if setting is None:
            if expected_revision != 0:
                raise SecretReferenceConflict()
            setting = KeywordSearchConfiguration()
            db.insert(setting)
        if setting.revision != expected_revision:
            raise SecretReferenceConflict()
        if destination is None:
            if reads_enabled or indexing_enabled:
                raise ValueError("A native keyword connection is required")
            connection = None
        else:
            reference = service.secret_reference._find(actor, secret_ref, lock=True)
            from ...core.security import KeyVault

            if (
                reference.scope != "instance"
                or int(reference.scope_id) != 0
                or reference.state != "active"
                or reference.revision != secret_revision
                or reference.provider != KeyVault.provider.name()
            ):
                raise SecretReferenceUnavailable()
            connection = db.exec(
                SqlBuilder.select.table(KeywordSearchConnection).where(
                    KeywordSearchConnection.endpoint == destination["endpoint"],
                    KeywordSearchConnection.index == destination["index"],
                    KeywordSearchConnection.secret_ref == reference.metadata()["uri"],
                    KeywordSearchConnection.secret_revision == secret_revision,
                    KeywordSearchConnection.active.is_(True),
                )
            ).first()
            if connection is None:
                connection = KeywordSearchConnection(
                    **destination, secret_ref=reference.metadata()["uri"], secret_revision=secret_revision
                )
                db.insert(connection)
        selected = connection.id if connection else None
        setting.connection_id = selected
        setting.reads_enabled = reads_enabled
        setting.indexing_enabled = indexing_enabled
        setting.revision += 1
        db.update(setting)
    return snapshot(actor)


def revoke_connection(actor, connection_uid, expected_revision):
    with DbSession.atomic() as db:
        require_admin(actor)
        setting = current_configuration(db, lock=True)
        if setting is None or setting.revision != expected_revision:
            raise SecretReferenceConflict()
        connection = db.exec(
            SqlBuilder.select.table(KeywordSearchConnection)
            .where(KeywordSearchConnection.id == InfraHelper.convert_id(connection_uid))
            .with_for_update()
        ).first()
        if connection is None:
            raise SecretReferenceUnavailable()
        connection.active = False
        db.update(connection)
        if setting.connection_id == connection.id:
            setting.reads_enabled = False
            setting.indexing_enabled = False
        setting.revision += 1
        db.update(setting)
        db.exec(
            update(DocumentKeywordIndex)
            .where(DocumentKeywordIndex.connection_id == connection.id)
            .values(
                version=DocumentKeywordIndex.version + 1, claim_token=None, last_error="original_connection_revoked"
            )
        )
    return snapshot(actor)


def snapshot(actor, *, cursor=None):
    require_admin(actor)
    if cursor is not None and (not isinstance(cursor, str) or not cursor.isalnum() or len(cursor) > 11):
        raise ValueError("Invalid connection cursor")
    with DbSession.use(readonly=False) as db:
        setting = current_configuration(db)
        statement = SqlBuilder.select.table(KeywordSearchConnection)
        if cursor:
            statement = statement.where(KeywordSearchConnection.id < InfraHelper.convert_id(cursor))
        page = db.exec(statement.order_by(KeywordSearchConnection.id.desc()).limit(51)).all()
        connections = page[:50]
        selected = (
            db.exec(
                SqlBuilder.select.table(KeywordSearchConnection).where(
                    KeywordSearchConnection.id == setting.connection_id
                )
            ).first()
            if setting and setting.connection_id is not None
            else None
        )
        counted_ids = list({item.id for item in connections} | ({selected.id} if selected else set()))
        # Aggregate only counts for this bounded connection page and legacy jobs.
        # Never hydrate document ownership rows in an administrator settings read.
        rows = db.exec(
            select(DocumentKeywordIndex.connection_id, DocumentKeywordIndex.operation, func.count())
            .where(
                DocumentKeywordIndex.due_at.is_not(None),
                or_(
                    DocumentKeywordIndex.connection_id.in_(counted_ids),
                    DocumentKeywordIndex.connection_id.is_(None),
                ),
            )
            .group_by(DocumentKeywordIndex.connection_id, DocumentKeywordIndex.operation)
        ).all()
        counts = {(connection_id, operation): count for connection_id, operation, count in rows}
        references = db.exec(
            SqlBuilder.select.table(SecretReference)
            .where(SecretReference.scope == "instance", SecretReference.scope_id == 0)
            .order_by(SecretReference.id.desc())
            .limit(101)
        ).all()

        from ...core.security import KeyVault

        identities = {
            int(InfraHelper.convert_id(item.secret_ref.rsplit("/", 1)[1]))
            for item in connections + ([selected] if selected else [])
        }
        grant_references = (
            db.exec(SqlBuilder.select.table(SecretReference).where(SecretReference.id.in_(identities))).all()
            if identities
            else []
        )
        grant_references = {int(reference.id): reference for reference in grant_references}

        def project_connection(item):
            reference = grant_references.get(int(InfraHelper.convert_id(item.secret_ref.rsplit("/", 1)[1])))
            reference_available = bool(
                reference
                and reference.scope == "instance"
                and int(reference.scope_id) == 0
                and reference.state == "active"
                and reference.revision == item.secret_revision
                and reference.provider == KeyVault.provider.name()
            )
            return {
                "uid": item.get_uid(),
                "endpoint": item.endpoint,
                "index": item.index,
                "secret_ref": item.secret_ref,
                "secret_revision": item.secret_revision,
                "active": item.active,
                "reference_available": reference_available,
                "pending_cleanup": counts.get((item.id, "remove"), 0),
                "blocked_index_jobs": counts.get((item.id, "index"), 0)
                if (
                    not item.active
                    or not reference_available
                    or setting is None
                    or setting.connection_id != item.id
                    or not setting.indexing_enabled
                )
                else 0,
            }

        return {
            "secret_references": [reference.metadata() for reference in references[:100]],
            "secret_references_truncated": len(references) > 100,
            "next_cursor": connections[-1].get_uid() if len(page) > 50 else None,
            "revision": setting.revision if setting else 0,
            "mode": "native" if setting else "legacy_environment",
            "reads_enabled": setting.reads_enabled if setting else enabled("read"),
            "indexing_enabled": setting.indexing_enabled if setting else enabled("index"),
            "selected_connection_uid": InfraHelper.convert_uid(setting.connection_id)
            if setting and setting.connection_id
            else None,
            "selected_connection": project_connection(selected) if selected else None,
            "connections": [project_connection(item) for item in connections],
            "legacy_pending_cleanup": counts.get((None, "remove"), 0),
        }
