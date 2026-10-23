"""Permission-gated native directory reads, independent of SCIM protocol authority."""

from typing import Any
from sqlalchemy import or_
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.types import SnowflakeID
from ....Env import Env
from ...models import ScimGroup, ScimGroupMember, User


class GroupDirectoryService(BaseDomainService):
    @staticmethod
    def name() -> str:
        return "group_directory"

    @staticmethod
    def _validate(page: int, limit: int, query: str) -> None:
        if not 1 <= page <= 1000000 or not 1 <= limit <= 100 or len(query) > 200:
            raise ValueError("Invalid directory pagination or search")

    @staticmethod
    def _search(columns: tuple, query: str) -> list:
        # Escape SQL wildcards as literal input on both SQLite and PostgreSQL.
        literal = query.replace("/", "//").replace("%", "/%").replace("_", "/_")
        return [or_(*(column.ilike(f"%{literal}%", escape="/") for column in columns))] if query else []

    @staticmethod
    def _group(group: ScimGroup) -> dict[str, Any]:
        current = group.belongs_to_scim(Env.SCIM_ISSUER)
        return {
            "uid": group.get_uid(),
            "display_name": group.display_name,
            "source": group.source,
            "issuer": group.issuer,
            "external_id": group.external_id,
            "source_status": (
                "current_authority" if current else "other_authority" if group.source == "scim" else group.source
            ),
            "managed": group.source == "scim",
            "read_only": True,
            "abilities": {"read_members": True, "update": False, "delete": False, "manage_members": False},
        }

    def list_groups(self, page: int = 1, limit: int = 15, query: str = "") -> dict[str, Any]:
        self._validate(page, limit, query)
        filters = self._search((ScimGroup.display_name, ScimGroup.external_id, ScimGroup.issuer), query)
        with DbSession.use(readonly=True) as db:
            total = db.exec(SqlBuilder.select.count(ScimGroup, ScimGroup.id).where(*filters)).first()
            groups = db.exec(
                SqlBuilder.select.table(ScimGroup)
                .where(*filters)
                .order_by(ScimGroup.display_name, ScimGroup.id)
                .offset((page - 1) * limit)
                .limit(limit)
            ).all()
        return {
            "records": [self._group(group) for group in groups],
            "total_records": total,
            "page": page,
            "limit": limit,
        }

    def list_members(self, group_uid: str, page: int = 1, limit: int = 15, query: str = "") -> dict[str, Any] | None:
        self._validate(page, limit, query)
        group_id = SnowflakeID.from_short_code(group_uid)
        filters = [
            ScimGroupMember.group_id == group_id,
            *self._search((User.firstname, User.lastname, User.username), query),
        ]
        with DbSession.use(readonly=True) as db:
            group = db.exec(SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == group_id)).first()
            if group is None:
                return None
            total = db.exec(
                SqlBuilder.select.count(ScimGroupMember, ScimGroupMember.id)
                .join(User, User.id == ScimGroupMember.user_id)
                .where(*filters)
            ).first()
            members = db.exec(
                SqlBuilder.select.columns(
                    User.id,
                    User.firstname,
                    User.lastname,
                    User.username,
                    User.activated_at,
                    User.deleted_at,
                    with_deleted=True,
                )
                .join(ScimGroupMember, ScimGroupMember.user_id == User.id)
                .where(*filters)
                .order_by(User.id)
                .offset((page - 1) * limit)
                .limit(limit)
            ).all()
        return {
            "group": self._group(group),
            "records": [
                {
                    "uid": user_id.to_short_code(),
                    "firstname": "" if deleted_at else firstname,
                    "lastname": "" if deleted_at else lastname,
                    "username": "" if deleted_at else username,
                    "account_status": "deleted" if deleted_at else "active" if activated_at else "inactive",
                }
                for user_id, firstname, lastname, username, activated_at, deleted_at in members
            ],
            "total_records": total,
            "page": page,
            "limit": limit,
        }
