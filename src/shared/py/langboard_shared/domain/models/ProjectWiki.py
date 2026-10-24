from typing import Any
from ...core.db import ApiField, EditorContentModel, Field, ModelColumnType, SnowflakeIDField, SoftDeleteModel
from ...core.types import SnowflakeID
from .Project import Project


class ProjectWiki(SoftDeleteModel, table=True):
    project_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Project, nullable=False, index=True, api_field=ApiField(name="project_uid")
    )
    title: str = Field(nullable=False, api_field=ApiField())
    content: EditorContentModel = Field(
        default=EditorContentModel(), sa_type=ModelColumnType(EditorContentModel), api_field=ApiField()
    )
    editor_generation: int = Field(default=0, nullable=False, api_field=ApiField())
    order: int = Field(default=0, nullable=False, api_field=ApiField())
    is_public: bool = Field(default=True, nullable=False, api_field=ApiField())

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema(
            {
                "forbidden": "bool",
                **(schema or {}),
            }
        )

    def api_response(self) -> dict[str, Any]:
        return {
            **super().api_response(),
            "forbidden": False,
        }

    def convert_to_private_api_response(self) -> dict[str, Any]:
        return {
            **self.api_response(),
            "title": "",
            "content": None,
            "is_public": False,
            "forbidden": True,
            "assigned_members": [],
        }

    def notification_data(self) -> dict[str, Any]:
        return {
            "uid": self.get_uid(),
            "title": self.title,
        }

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["project_id", "title", "order", "is_public"]
