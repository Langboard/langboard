from typing import Any
from ..core.publisher import BaseSocketPublisher, SocketPublishModel
from ..core.routing import SocketTopic
from ..core.utils.decorators import staticclass
from ..domain.models import Project


@staticclass
class GraphApprovalPublisher(BaseSocketPublisher):
    @staticmethod
    def requested(project: Project, approval: dict[str, Any]) -> None:
        GraphApprovalPublisher.__publish(project, approval, "board:graph:approval:requested")

    @staticmethod
    def updated(project: Project, approval: dict[str, Any]) -> None:
        GraphApprovalPublisher.__publish(project, approval, "board:graph:approval:updated")

    @staticmethod
    def deleted(project: Project, approval_uid: str) -> None:
        topic_id = project.get_uid()
        model = {"uid": approval_uid}
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event="board:graph:approval:deleted",
            data_keys=list(model.keys()),
        )
        GraphApprovalPublisher.put_dispather(model, publish_model)

    @staticmethod
    def __publish(project: Project, approval: dict[str, Any], event: str) -> None:
        topic_id = project.get_uid()
        model = {"approval": approval}
        publish_model = SocketPublishModel(
            topic=SocketTopic.BoardSettings,
            topic_id=topic_id,
            event=event,
            data_keys=list(model.keys()),
        )
        GraphApprovalPublisher.put_dispather(model, publish_model)
