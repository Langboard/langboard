from json import dumps as json_dumps
from json import loads as json_loads
from .....ai import LangboardCalledVariablesComponent
from .....ai.TweaksComponent import TweaksComponent
from .....core.logger import Logger
from .....core.utils.Converter import json_default
from .....domain.models import BotLog
from .....domain.models.BaseBotModel import BotPlatformRunningType
from .....domain.models.bases import BaseBotLogModel
from ..BotTaskDataHelper import BotTaskDataHelper
from .BaseBotRequest import BaseBotRequest, RequestData


logger = Logger.use("bot-task")


class LangflowRequest(BaseBotRequest):
    def create_request_data(self, bot_log: tuple[BotLog, BaseBotLogModel | None]):
        if self._bot.platform_running_type != BotPlatformRunningType.Endpoint:
            return None

        project_uid = self._project.get_uid() if self._project else None
        session_id = f"{self._bot.get_uid()}-{project_uid}"
        if session_id.endswith("-"):
            session_id = session_id[:-1]

        log, scope_log = bot_log

        components: list[TweaksComponent] = [
            LangboardCalledVariablesComponent(
                event=self._event,
                app_api_token=self._bot.app_api_token,
                project_uid=project_uid,
                current_runner_type="bot",
                current_runner_data=BotTaskDataHelper.create_user_or_bot(self._bot),
                rest_data=json_loads(json_dumps(self._data, default=json_default)),
            ),
        ]

        request_data = {
            "input_value": "",
            "input_type": "chat",
            "output_type": "chat",
            "session": session_id,
            "session_id": session_id,
            "run_type": "bot",
            "uid": self._bot.get_uid(),
            "project_uid": project_uid,
            "log_uid": log.get_uid(),
            "scope_log_table": scope_log.__tablename__ if scope_log else None,
            "tweaks": {},
        }

        for component in components:
            request_data["tweaks"].update(component.to_data())
            request_data["tweaks"].update(component.to_tweaks())

        url = f"{self._base_url}/{self._bot.value.lstrip('/')}"

        return RequestData(
            {
                "url": url,
                "data": request_data,
            }
        )
