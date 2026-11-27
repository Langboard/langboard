from .bases import BaseGraphApprovalBotRequest
from .GraphApprovalRequest import GraphApprovalOriginType


class BotScheduleGraphApprovalRequest(BaseGraphApprovalBotRequest, table=True):
    @staticmethod
    def get_request_type() -> GraphApprovalOriginType:
        return GraphApprovalOriginType.Schedule
