from typing import Any
from ....core.utils.decorators import staticclass
from ....domain.constants.DomainEventType import DomainEventType
from ....domain.models import Bot, User
from ...webhooks.utils import WebhookDataHelper


@staticclass
class BotTaskSchemaHelper:
    @staticmethod
    def schema(condition: DomainEventType, schema: dict[str, Any] | None = None):
        return WebhookDataHelper.schema(condition.value, schema)

    @staticmethod
    def executor_schema(condition: DomainEventType, schema: dict[str, Any] | None = None):
        return BotTaskSchemaHelper.schema(
            condition, {"executor": BotTaskSchemaHelper.create_user_or_bot_schema(), **(schema or {})}
        )

    @staticmethod
    def project_schema(condition: DomainEventType, schema: dict[str, Any] | None = None):
        return BotTaskSchemaHelper.schema(
            condition,
            {
                "project_uid": "string",
                "project_title": "string",
                "executor": BotTaskSchemaHelper.create_user_or_bot_schema(),
                **(schema or {}),
            },
        )

    @staticmethod
    def project_column_schema(condition: DomainEventType, schema: dict[str, Any] | None = None):
        return BotTaskSchemaHelper.schema(
            condition,
            {
                "project_uid": "string",
                "project_title": "string",
                "project_column_uid": "string",
                "project_column_name": "string",
                "project_column_is_archive": "boolean",
                "executor": BotTaskSchemaHelper.create_user_or_bot_schema(),
                **(schema or {}),
            },
        )

    @staticmethod
    def card_schema(condition: DomainEventType, schema: dict[str, Any] | None = None):
        return BotTaskSchemaHelper.schema(
            condition,
            {
                "project_uid": "string",
                "project_title": "string",
                "project_column_uid": "string",
                "project_column_name": "string",
                "project_column_is_archive": "boolean",
                "card_uid": "string",
                "card_title": "string",
                "related_cards": {
                    "parents": [BotTaskSchemaHelper.create_related_card_schema()],
                    "children": [BotTaskSchemaHelper.create_related_card_schema()],
                },
                "executor": BotTaskSchemaHelper.create_user_or_bot_schema(),
                **(schema or {}),
            },
        )

    @staticmethod
    def create_related_card_schema():
        return {
            "card_uid": "string",
            "title": "string",
            "project_column_uid": "string",
            "archived_at": "datetime?",
            "relationship_type_uid": "string",
            "relationship_name": "string",
            "relationship_description": "string",
        }

    @staticmethod
    def changes_schema(*fields: tuple[str, str | dict]):
        changes = {}
        for field, type_ in fields:
            changes[f"old_{field}"] = type_
            changes[f"new_{field}"] = type_
        return {"changes": changes}

    @staticmethod
    def create_user_or_bot_schema():
        return {"oneOf": {"User": User.api_schema(), "Bot": Bot.api_schema()}}
