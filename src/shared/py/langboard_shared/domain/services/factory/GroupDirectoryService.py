"""Permission-gated native directory reads, independent of SCIM protocol authority."""

import re
from contextlib import contextmanager
from typing import Any
from sqlalchemy import delete, or_, text
from sqlalchemy.exc import OperationalError
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....core.types import SnowflakeID
from ....Env import Env
from ...models import GroupDirectoryAudit, Organization, ScimGroup, ScimGroupMember, SettingRole, User
from ...models.SettingRole import SettingRoleAction


class GroupDirectoryDenied(Exception):
    pass


class GroupDirectoryConflict(Exception):
    pass


class GroupDirectoryMissing(Exception):
    pass


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
    def _group(group: ScimGroup, can_manage: bool = False) -> dict[str, Any]:
        writable = can_manage and group.source == "local"
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
            "revision": group.revision,
            "read_only": not writable,
            "abilities": {"read_members": True, "update": writable, "delete": writable, "manage_members": writable},
        }

    def list_groups(self, page: int = 1, limit: int = 15, query: str = "", actor_id=None) -> dict[str, Any]:
        self._validate(page, limit, query)
        can_manage = self.can_manage(actor_id)
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
            "records": [self._group(group, can_manage) for group in groups],
            "total_records": total,
            "page": page,
            "limit": limit,
        }

    def list_members(
        self, group_uid: str, page: int = 1, limit: int = 15, query: str = "", actor_id=None
    ) -> dict[str, Any] | None:
        self._validate(page, limit, query)
        group_id = SnowflakeID.from_short_code(group_uid)
        filters = [
            ScimGroupMember.group_id == group_id,
            *self._search((User.firstname, User.lastname, User.username), query),
        ]
        # Deleted identities are redacted below; search must not expose them
        # through matching rows or counts. Keep their membership on unfiltered reads.
        if query:
            filters.append(User.deleted_at.is_(None))
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
            "group": self._group(group, self.can_manage(actor_id)),
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

    @staticmethod
    def _authorized(db, actor_id, action=SettingRoleAction.GroupManage):
        actor = db.exec(SqlBuilder.select.table(User, with_deleted=True).where(User.id == actor_id)).first()
        if not actor or actor.deleted_at or not actor.activated_at or not actor.is_admin:
            raise GroupDirectoryDenied()
        roles = db.exec(SqlBuilder.select.table(SettingRole).where(SettingRole.user_id == actor.id)).all()
        if actor.email not in Env.FULL_ADMIN_ACCESS_EMAILS and not any(role.is_granted(action) for role in roles):
            raise GroupDirectoryDenied()
        return actor

    @staticmethod
    def _tenant(db):
        organizations = db.exec(SqlBuilder.select.table(Organization)).all()
        if len(organizations) != 1 or not organizations[0].is_active or organizations[0].suspended_at:
            raise GroupDirectoryDenied()

    def can_manage(self, actor_id):
        if actor_id is None:
            return False
        with DbSession.use(readonly=False) as db:
            try:
                self._authorized(db, actor_id)
                self._tenant(db)
            except GroupDirectoryDenied:
                return False
        return True

    @contextmanager
    def _management(self, actor_id):
        try:
            with DbSession.atomic() as db:
                dialect = DbEngine.get_main_engine().dialect.name
                # Table locks cover absent roles and all native account/role/source writers,
                # which need not cooperate with a directory-specific advisory lock.
                if dialect == "sqlite":
                    db.exec(text("BEGIN IMMEDIATE"))
                elif dialect == "postgresql":
                    db.exec(text("SET LOCAL lock_timeout = '3s'"))
                    db.exec(text("SET LOCAL statement_timeout = '10s'"))
                    db.exec(
                        text(
                            'LOCK TABLE organization, "user", setting_role, scim_group, scim_group_member IN SHARE ROW EXCLUSIVE MODE'
                        )
                    )
                else:
                    raise RuntimeError("Group management supports SQLite and PostgreSQL")
                actor = self._authorized(db, actor_id)
                self._tenant(db)
                yield db, actor
        except OperationalError as exc:
            code = getattr(exc.orig, "sqlstate", None)
            if code in ("55P03", "57014") or (dialect == "sqlite" and "locked" in str(exc.orig).lower()):
                raise GroupDirectoryConflict() from exc
            raise

    @staticmethod
    def _uid(uid):
        if not isinstance(uid, str) or not re.fullmatch(r"[0-9A-Za-z]{11}", uid):
            raise ValueError("Invalid user or group UID")
        return SnowflakeID.from_short_code(uid)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Invalid group name")
        return name.strip()

    def mutate(self, actor_id, action, group_uid=None, expected_revision=None, display_name=None, user_uids=None):
        if action not in ("create", "rename", "delete", "add_members", "remove_members"):
            raise ValueError("Invalid group action")
        name = self._name(display_name) if action in ("create", "rename") else None
        group_id = self._uid(group_uid) if action != "create" else None
        if action != "create" and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("Invalid expected revision")
        member_ids = []
        if action in ("add_members", "remove_members"):
            if not isinstance(user_uids, list) or not 1 <= len(user_uids) <= 100:
                raise ValueError("Invalid member delta")
            member_ids = sorted({self._uid(uid) for uid in user_uids})
        with self._management(actor_id) as (db, actor):
            if action == "create":
                group = ScimGroup(source="local", issuer="", display_name=name)
                db.insert(group)
                changes = {"display_name": name}
            else:
                group = db.exec(SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == group_id)).first()
                if group is None:
                    raise GroupDirectoryMissing()
                if group.source != "local":
                    raise GroupDirectoryDenied()
                if group.revision != expected_revision:
                    raise GroupDirectoryConflict()
                group.revision += 1
                changes = {}
                if action == "rename":
                    changes = {"display_name": {"before": group.display_name, "after": name}}
                    group.display_name = name
                if member_ids:
                    if action == "add_members":
                        users = db.exec(
                            SqlBuilder.select.table(User).where(
                                User.id.in_(member_ids), User.activated_at.is_not(None), User.deleted_at.is_(None)
                            )
                        ).all()
                        if {user.id for user in users} != set(member_ids):
                            raise ValueError("Members must be active users")
                    existing = db.exec(
                        SqlBuilder.select.table(ScimGroupMember).where(
                            ScimGroupMember.group_id == group.id, ScimGroupMember.user_id.in_(member_ids)
                        )
                    ).all()
                    existing_ids = {member.user_id for member in existing}
                    changed_ids = set(member_ids) - existing_ids if action == "add_members" else existing_ids
                    if action == "add_members":
                        for user_id in sorted(changed_ids):
                            db.insert(ScimGroupMember(group_id=group.id, user_id=user_id))
                    else:
                        db.exec(
                            delete(ScimGroupMember).where(
                                ScimGroupMember.group_id == group.id, ScimGroupMember.user_id.in_(member_ids)
                            )
                        )
                    changes = {"user_uids": [uid.to_short_code() for uid in sorted(changed_ids)]}
                if action == "delete":
                    changes = {"display_name": group.display_name}
                    db.exec(delete(ScimGroupMember).where(ScimGroupMember.group_id == group.id))
                    db.delete(group)
                else:
                    db.update(group)
            db.insert(
                GroupDirectoryAudit(
                    actor_uid=actor.get_uid(),
                    group_uid=group.get_uid(),
                    action=action,
                    revision=group.revision,
                    change_facts=changes,
                )
            )
            return (
                {"deleted": True, "group_uid": group.get_uid()}
                if action == "delete"
                else {"group": self._group(group, True)}
            )

    def candidates(self, actor_id, page=1, limit=15, query=""):
        self._validate(page, limit, query)
        with self._management(actor_id) as (db, _):
            search = self._search((User.firstname, User.lastname, User.username), query)
            if re.fullmatch(r"[0-9A-Za-z]{11}", query):
                search = [or_(User.id == self._uid(query), *search)]
            filters = [User.activated_at.is_not(None), User.deleted_at.is_(None), *search]
            total = db.exec(SqlBuilder.select.count(User, User.id).where(*filters)).first()
            users = db.exec(
                SqlBuilder.select.columns(User.id, User.firstname, User.lastname, User.username)
                .where(*filters)
                .order_by(User.id)
                .offset((page - 1) * limit)
                .limit(limit)
            ).all()
            return {
                "records": [
                    {
                        "uid": uid.to_short_code(),
                        "firstname": first,
                        "lastname": last,
                        "username": username,
                        "account_status": "active",
                    }
                    for uid, first, last, username in users
                ],
                "total_records": total,
                "page": page,
                "limit": limit,
            }

    def history(self, actor_id, group_uid, page=1, limit=15):
        self._validate(page, limit, "")
        self._uid(group_uid)
        with DbSession.atomic() as db:
            self._authorized(db, actor_id, SettingRoleAction.GroupRead)
            filters = [GroupDirectoryAudit.group_uid == group_uid]
            total = db.exec(
                SqlBuilder.select.count(GroupDirectoryAudit, GroupDirectoryAudit.id).where(*filters)
            ).first()
            records = db.exec(
                SqlBuilder.select.table(GroupDirectoryAudit)
                .where(*filters)
                .order_by(GroupDirectoryAudit.revision.desc(), GroupDirectoryAudit.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            ).all()
            if (
                not total
                and not db.exec(SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == self._uid(group_uid))).first()
            ):
                raise GroupDirectoryMissing()
            return {
                "records": [
                    {
                        "uid": record.get_uid(),
                        "actor_uid": record.actor_uid,
                        "group_uid": record.group_uid,
                        "action": record.action,
                        "revision": record.revision,
                        "changes": (
                            {"target_count": record.change_facts.get("target_count", 0)}
                            if record.action == "board_invitation"
                            else record.change_facts
                        ),
                        "created_at": record.created_at.isoformat(),
                    }
                    for record in records
                ],
                "total_records": total,
                "page": page,
                "limit": limit,
            }
