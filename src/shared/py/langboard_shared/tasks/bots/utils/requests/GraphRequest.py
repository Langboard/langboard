from typing import Any
from urllib.parse import quote
from .BaseBotRequest import BaseBotRequest, RequestData


class GraphRequest(BaseBotRequest):
    def _create_graph_webhook_request_data(self, request_data: dict[str, Any]) -> RequestData:
        session_id = str(request_data["session_id"])
        return RequestData(
            {
                "url": f"{self._base_url}/api/v1/graph/webhook/{quote(session_id, safe='')}",
                "data": request_data,
            }
        )

    def _create_bot_graph_thread_id(
        self,
        project_uid: str | None,
        session_id: str,
        rest_data: dict[str, Any],
    ) -> str:
        scope_type = "project"
        scope_uid = project_uid or "global"
        if rest_data.get("card_uid"):
            scope_type = "card"
            scope_uid = rest_data["card_uid"]
        elif rest_data.get("project_wiki_uid"):
            scope_type = "project_wiki"
            scope_uid = rest_data["project_wiki_uid"]
        elif rest_data.get("project_column_uid"):
            scope_type = "project_column"
            scope_uid = rest_data["project_column_uid"]

        return ":".join([self._bot.get_uid(), project_uid or "global", scope_type, str(scope_uid), session_id])
