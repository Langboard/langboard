from datetime import datetime, timezone
from sqlalchemy import select
from ....core.db import DbSession
from ....core.domain import BaseRepository
from ....core.types import SnowflakeID
from ....domain.models import User, UserSignInHistory


class UserSignInHistoryRepository(BaseRepository[UserSignInHistory]):
    @staticmethod
    def model_cls():
        return UserSignInHistory

    @staticmethod
    def name() -> str:
        return "user_sign_in_history"

    def get_page_in_settings(
        self,
        user_uid: str,
        *,
        page: int,
        limit: int,
        is_success: bool | None = None,
        occurred_from: datetime | None = None,
        occurred_before: datetime | None = None,
    ) -> dict | None:
        user_id = SnowflakeID.from_short_code(user_uid)
        filters = [UserSignInHistory.user_id == user_id, User.deleted_at.is_(None)]
        if is_success is not None:
            filters.append(UserSignInHistory.is_success == is_success)
        if occurred_from is not None:
            filters.append(UserSignInHistory.signed_in_at >= occurred_from)
        if occurred_before is not None:
            filters.append(UserSignInHistory.signed_in_at < occurred_before)
        # Project only audit-safe fields; never load IP addresses or account secrets.
        query = (
            select(
                UserSignInHistory.id,
                UserSignInHistory.signed_in_at,
                UserSignInHistory.is_success,
                UserSignInHistory.error_code,
            )
            .join(User, User.id == UserSignInHistory.user_id)
            .where(*filters)
            .order_by(UserSignInHistory.signed_in_at.desc(), UserSignInHistory.id.desc())
            .offset((page - 1) * limit)
            .limit(limit + 1)
        )
        # Current account lifecycle and audit records come from the primary database.
        with DbSession.use(readonly=False) as db:
            target = db.exec(select(User.id).where(User.id == user_id, User.deleted_at.is_(None))).first()
            if target is None:
                return None
            rows = db.exec(query).all()
        return {
            "records": [
                {
                    "event_id": event_id.to_short_code(),
                    "occurred_at": occurred_at.replace(tzinfo=timezone.utc)
                    if occurred_at.tzinfo is None
                    else occurred_at,
                    "is_success": success,
                    "error_code": error_code.value if error_code else None,
                }
                for event_id, occurred_at, success, error_code in rows[:limit]
            ],
            "page": page,
            "limit": limit,
            "has_more": len(rows) > limit,
        }
