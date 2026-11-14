from typing import Any
from ...core.db import ApiField, Field, ModelColumnType, SnowflakeIDField, SoftDeleteModel
from ...core.storage import FileModel
from ...core.types import SnowflakeID
from .ProjectWiki import ProjectWiki
from .User import User


class ProjectWikiAttachment(SoftDeleteModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    project_wiki_id: SnowflakeID = SnowflakeIDField(foreign_key=ProjectWiki, nullable=False, index=True)
    filename: str = Field(nullable=False, api_field=ApiField(name="name"))
    file: FileModel = Field(sa_type=ModelColumnType(FileModel), api_field=ApiField(name="url", field_base_model="path"))
    content_sha256: str | None = Field(default=None, nullable=True, max_length=64, exclude=True)
    # Historical objects resolve directly to the stable active attachment.
    replaced_by_id: SnowflakeID | None = SnowflakeIDField(nullable=True, index=True)
    order: int = Field(default=0, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["user_id", "project_wiki_id", "filename", "file"]
