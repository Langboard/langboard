from typing import Any
from ..core.publisher import BaseSocketPublisher, SocketPublishModel
from ..core.routing import SocketTopic
from ..core.utils.decorators import staticclass
from ..domain.models import Bot, Project, ProjectWiki, User


@staticclass
class ProjectWikiPublisher(BaseSocketPublisher):
    @staticmethod
    def created(project: Project, wiki: ProjectWiki):
        metadata = wiki.api_response()
        metadata.pop("content", None)
        metadata.pop("editor_generation", None)
        model = {"wiki": {**metadata, "assigned_members": []}}
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardWiki,
            topic_id=project.get_uid(),
            event=f"board:wiki:created:{project.get_uid()}",
            data_keys="wiki",
        )

        ProjectWikiPublisher.put_dispather(model, publish_model)

    @staticmethod
    def updated(project: Project, wiki: ProjectWiki, model: dict[str, Any]):
        # Preserve content-only invalidation in the wiki's current editor lineage.
        if "content" in model:
            model = {**model, "editor_generation": wiki.editor_generation}
        # Durable socket messages contain invalidation metadata, never editor snapshots.
        model = {key: value for key, value in model.items() if key != "content"}
        wiki_uid = wiki.get_uid()
        topic = SocketTopic.BoardWiki if wiki.is_public else SocketTopic.BoardWikiPrivate
        topic_id = project.get_uid() if wiki.is_public else wiki_uid
        publish_model = SocketPublishModel(
            topic=topic,
            topic_id=topic_id,
            event=f"board:wiki:details:changed:{wiki_uid}",
            data_keys=list(model.keys()),
        )

        ProjectWikiPublisher.put_dispather(model, publish_model)

    @staticmethod
    def publicity_changed(user_or_bot: User | Bot, project: Project, wiki: ProjectWiki):
        assigned_users = [user_or_bot] if not wiki.is_public and isinstance(user_or_bot, User) else []

        model = {
            "wiki": {
                # A publicity request may hold a snapshot from before secret conversion.
                # Preserve the legacy wiki envelope, but never transport editor state.
                "uid": wiki.get_uid(),
                "is_public": wiki.is_public,
                "assigned_members": [assigned_user.api_response() for assigned_user in assigned_users],
            },
        }

        wiki_uid = wiki.get_uid()
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardWiki,
            topic_id=project.get_uid(),
            event=f"board:wiki:public:changed:{wiki_uid}",
            data_keys=list(model.keys()),
        )

        ProjectWikiPublisher.put_dispather(model, publish_model)

    @staticmethod
    def assignees_updated(project: Project, wiki: ProjectWiki, users: list[User]):
        model = {
            "assigned_members": [user.api_response() for user in users],
        }
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardWiki,
            topic_id=project.get_uid(),
            event=f"board:wiki:assignees:updated:{wiki.get_uid()}",
            data_keys=list(model.keys()),
        )

        ProjectWikiPublisher.put_dispather(model, publish_model)

    @staticmethod
    def order_changed(project: Project, wiki: ProjectWiki):
        model = {
            "uid": wiki.get_uid(),
            "order": wiki.order,
        }
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardWiki,
            topic_id=project.get_uid(),
            event=f"board:wiki:order:changed:{project.get_uid()}",
            data_keys=list(model.keys()),
        )

        ProjectWikiPublisher.put_dispather(model, publish_model)

    @staticmethod
    def deleted(project: Project, wiki: ProjectWiki):
        model = {
            "uid": wiki.get_uid(),
        }
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardWiki,
            topic_id=project.get_uid(),
            event=f"board:wiki:deleted:{project.get_uid()}",
            data_keys=list(model.keys()),
        )

        ProjectWikiPublisher.put_dispather(model, publish_model)
