from typing import Any
from ....core.db import BaseDbModel, DbSession, EditorContentModel, SqlBuilder
from ....core.types import SnowflakeID
from ....core.utils.Converter import convert_python_data
from ....core.utils.decorators import staticclass
from ....core.utils.EditorContentParser import find_mentioned
from ....domain.models import (
    Bot,
    Card,
    CardAttachment,
    CardComment,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    Project,
    ProjectColumn,
    ProjectLabel,
    ProjectWiki,
    User,
)


@staticclass
class ActivityHistoryHelper:
    @staticmethod
    def create_user_or_bot_history(user_or_bot: User | Bot):
        avatar_path = None
        if user_or_bot.avatar:
            avatar_path = user_or_bot.avatar.path
        if isinstance(user_or_bot, User):
            if user_or_bot.deleted_at:
                return {
                    "type": User.UNKNOWN_USER_TYPE,
                    "firstname": "",
                    "lastname": "",
                    "avatar": None,
                }
            return {
                "type": User.USER_TYPE,
                "firstname": user_or_bot.firstname,
                "lastname": user_or_bot.lastname,
                "avatar": avatar_path,
            }
        else:
            return {
                "type": Bot.BOT_TYPE,
                "name": user_or_bot.name,
                "avatar": avatar_path,
            }

    @staticmethod
    def create_project_history(project: Project):
        return {
            "title": project.title,
            "project_type": project.project_type,
        }

    @staticmethod
    def create_project_column_history(column: ProjectColumn):
        return {
            "name": column.name,
        }

    @staticmethod
    def create_label_history(label: ProjectLabel):
        return {
            "name": label.name,
            "color": label.color,
        }

    @staticmethod
    def create_project_wiki_history(wiki: ProjectWiki):
        return {
            "title": wiki.title,
        }

    @staticmethod
    def create_card_history(card: Card, column: ProjectColumn):
        history = {
            "card": {
                "title": card.title,
            }
        }
        history["column"] = ActivityHistoryHelper.create_project_column_history(column)
        return history

    @staticmethod
    def create_card_attachment_history(attachment: CardAttachment):
        return {
            "name": attachment.filename,
            "url": attachment.file["path"] if isinstance(attachment.file, dict) else attachment.file.path,
        }

    @staticmethod
    def create_checklist_or_checkitem_history(check: Checklist | Checkitem):
        return {
            "title": check.title,
        }

    @staticmethod
    def create_card_comment_history(comment: CardComment, user_or_bot: User | Bot):
        return {
            "content": ActivityHistoryHelper.convert_to_python(comment.content),
            "author": ActivityHistoryHelper.create_user_or_bot_history(user_or_bot),
        }

    @staticmethod
    def create_card_relationship(global_relationship: GlobalCardRelationshipType, related_card: Card, is_parent: bool):
        return {
            "relationship_name": global_relationship.parent_name if is_parent else global_relationship.child_name,
            "related_card_title": related_card.title,
        }

    @staticmethod
    def create_changes(before: dict[str, Any], model: BaseDbModel):
        after = {key: getattr(model, key) for key in before}
        for key in before:
            before[key] = ActivityHistoryHelper.convert_to_python(before[key])
            after[key] = ActivityHistoryHelper.convert_to_python(after[key])

        return {"changes": {"before": before, "after": after}}

    @staticmethod
    def convert_to_python(data: Any) -> Any:
        if isinstance(data, EditorContentModel) or (isinstance(data, dict) and "content" in data):
            new_data = data if isinstance(data, EditorContentModel) else EditorContentModel(**data)
            user_or_bot_uids, _ = find_mentioned(new_data)
            user_or_bot_ids = [SnowflakeID.from_short_code(uid) for uid in user_or_bot_uids]
            mentionables: list[Bot | User] = []
            with DbSession.use(readonly=True) as db:
                result = db.exec(SqlBuilder.select.table(User).where(User.column("id").in_(user_or_bot_ids)))
                mentionables.extend(result.all())

            with DbSession.use(readonly=True) as db:
                result = db.exec(SqlBuilder.select.table(Bot).where(Bot.column("id").in_(user_or_bot_ids)))
                mentionables.extend(result.all())

            return {
                "type": "editor",
                "mentionables": [
                    ActivityHistoryHelper.create_user_or_bot_history(mentionable) for mentionable in mentionables
                ],
                **new_data.model_dump(),
            }

        return convert_python_data(data)
