from .bases import BaseGraphApprovalBotRequest
from .GraphApprovalRequest import GraphApprovalOriginType


class BotTriggerGraphApprovalRequest(BaseGraphApprovalBotRequest, table=True):
    @staticmethod
    def get_request_type() -> GraphApprovalOriginType:
        return GraphApprovalOriginType.Trigger
