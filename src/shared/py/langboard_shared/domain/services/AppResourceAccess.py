"""Explicit connection revocation shared by external provider adapters."""

from sqlalchemy import or_
from ...core.db import SqlBuilder
from ..models import AppResourceBinding


def revoke_connection_resources(db, connection_id):
    """Revoke stale grants even after disconnect, without advancing revoked revisions."""
    return db.exec(
        SqlBuilder.update.table(AppResourceBinding)
        .where(
            AppResourceBinding.connection_id == connection_id,
            or_(AppResourceBinding.access_state != "revoked", AppResourceBinding.health != "unavailable"),
        )
        .values(access_state="revoked", health="unavailable", access_revision=AppResourceBinding.access_revision + 1)
    )
