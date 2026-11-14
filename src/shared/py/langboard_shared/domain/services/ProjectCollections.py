"""Revision-checked personal collections, preserving existing board identity."""

from uuid import UUID
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import UserProjectCollection
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import _actor, _authorized_boards
from .UserAppPreferences import PreferenceConflict


class ProjectCollection(BaseModel):
    model_config = {"extra": "forbid"}
    uid: str = Field(min_length=36, max_length=36, strict=True)
    name: str = Field(min_length=1, max_length=100, strict=True)
    board_uids: list[str] = Field(max_length=128)
    starred: bool = Field(default=False, strict=True)

    @field_validator("uid")
    @classmethod
    def valid_uid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Collection UID must be a canonical UUID")
        return value

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Collection name must not be blank")
        return value

    @field_validator("board_uids")
    @classmethod
    def unique_boards(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("A board can occur only once in a collection")
        for value in values:
            if len(value) != 11 or not value.isascii() or not value.isalnum():
                raise ValueError("Invalid board UID")
            SnowflakeID.from_short_code(value)
        return values


def project_collections(actor, *, collections=None, expected_revision=None):
    if expected_revision is not None:
        parsed = TypeAdapter(list[ProjectCollection]).validate_python(collections)
        if len(parsed) > 64 or len({item.uid for item in parsed}) != len(parsed):
            raise ValueError("Collections must be bounded and unique")
        if sum(len(item.board_uids) for item in parsed) > 1024:
            raise ValueError("Too many board references")
        collections = [item.model_dump() for item in parsed]
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=expected_revision is not None)
        row = db.exec(SqlBuilder.select.table(UserProjectCollection).where(UserProjectCollection.user_id == actor.id)).first()
        revision = row.revision if row else 0
        if expected_revision is not None:
            if revision != expected_revision:
                raise PreferenceConflict()
            previous = {uid for item in (row.collections if row else []) for uid in item["board_uids"]}
            requested = {uid for item in collections for uid in item["board_uids"]}
            # Retain/remove revoked references without making saved groups uneditable.
            # Their names/content must still come from the permission-scoped board query.
            _authorized_boards(
                db,
                actor,
                [SnowflakeID.from_short_code(uid) for uid in requested - previous],
                action=ProjectRoleAction.Read,
            )
            if row:
                changed = db.exec(
                    update(UserProjectCollection)
                    .where(UserProjectCollection.id == row.id, UserProjectCollection.revision == expected_revision)
                    .values(collections=collections, revision=expected_revision + 1, updated_at=SafeDateTime.now())
                )
                if changed != 1:
                    raise PreferenceConflict()
            else:
                db.insert(UserProjectCollection(user_id=actor.id, collections=collections))
            return {"revision": expected_revision + 1, "collections": collections}
        return {
            "revision": revision,
            "collections": [ProjectCollection.model_validate(item).model_dump() for item in row.collections] if row else [],
        }
