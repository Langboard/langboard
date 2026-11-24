"""A bounded account preference with optimistic concurrency across devices."""

from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import UserAppPreference
from .AppGovernance import _actor


class PreferenceConflict(Exception):
    pass


def app_preferences(actor, *, pinned_apps=None, expected_revision=None):
    with DbSession.atomic() as db:
        # The account row also serializes first writes before a preference exists.
        actor = _actor(db, actor, for_update=expected_revision is not None)
        row = db.exec(SqlBuilder.select.table(UserAppPreference).where(UserAppPreference.user_id == actor.id)).first()
        revision = row.revision if row else 0
        if expected_revision is not None:
            if revision != expected_revision:
                raise PreferenceConflict()
            if row:
                count = db.exec(
                    update(UserAppPreference)
                    .where(UserAppPreference.id == row.id, UserAppPreference.revision == expected_revision)
                    .values(pinned_apps=pinned_apps, revision=expected_revision + 1, updated_at=SafeDateTime.now())
                )
                if count != 1:
                    raise PreferenceConflict()
                return {"revision": expected_revision + 1, "pinned_apps": pinned_apps}
            else:
                row = UserAppPreference(user_id=actor.id, pinned_apps=pinned_apps)
                db.insert(row)
        return {"revision": row.revision if row else 0, "pinned_apps": row.pinned_apps if row else []}
