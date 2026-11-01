"""Native change units and consistent authorized board snapshots."""

from copy import deepcopy
from functools import wraps
from typing import Any, Callable
from sqlalchemy import text
from ...core.db import DbSession
from ...core.db.DbEngine import DbEngine
from ..models import User


def dispatch_after_commit(effect: Callable[..., Any], *args, **kwargs) -> None:
    """Defer only transport work; freeze this mutation's payload at registration.

    Domain, notification and metadata writes must run before this boundary.
    Standalone dispatch keeps its existing immediate behavior.
    """
    if not DbSession.has_active_transaction():
        effect(*args, **kwargs)
        return
    prepare = vars(effect).get("_workflow_prepare") if hasattr(effect, "__dict__") else None
    if prepare is not None:
        prepare(*args, **kwargs)
        effect = effect._workflow_transport
    frozen_args, frozen_kwargs = deepcopy((args, kwargs))
    with DbSession.atomic() as db:
        db.after_commit(lambda: effect(*frozen_args, **frozen_kwargs))


def card_change_transaction(operation):
    """Include the domain write, ordered card stamp and receipt in one commit."""
    @wraps(operation)
    def atomic(*args, **kwargs):
        with DbSession.atomic():
            return operation(*args, **kwargs)
    return atomic


def card_change_snapshot(operation):
    """Resolve authority, rows, read cursors and counts from one primary snapshot.

    Board requests own this read unit. Nesting inside a write unit would inherit
    its isolation, so reject that unsupported call rather than claim consistency.
    """
    @wraps(operation)
    def snapshot(self, project, user_or_bot=None, *args, **kwargs):
        if not isinstance(user_or_bot, User):
            return operation(self, project, user_or_bot, *args, **kwargs)
        if DbSession.has_active_transaction():
            raise RuntimeError("Board change snapshot must own its database transaction")
        with DbSession.atomic() as db:
            dialect = DbEngine.get_main_engine().dialect.name
            if dialect == "postgresql":
                db.exec(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            elif dialect == "sqlite":
                # sqlite3 legacy mode does not begin a snapshot on SELECT.
                db.exec(text("BEGIN"))
            return operation(self, project, user_or_bot, *args, **kwargs)
    return snapshot
