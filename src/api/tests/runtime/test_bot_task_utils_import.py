"""Bot task helpers remain classes during package initialization."""

from langboard_shared.tasks.bots.utils import BotTaskSchemaHelper


def test_bot_task_schema_helper_is_exported_as_class():
    assert BotTaskSchemaHelper.__name__ == "BotTaskSchemaHelper"
    assert callable(BotTaskSchemaHelper.card_schema)
