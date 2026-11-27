from .bases import BaseGraphApprovalBotRequest
from .GraphApprovalRequest import GraphApprovalOriginType


class ManualScopeRunGraphApprovalRequest(BaseGraphApprovalBotRequest, table=True):
    @staticmethod
    def get_request_type() -> GraphApprovalOriginType:
        return GraphApprovalOriginType.ManualScopeRun
