from json import dumps as json_dumps
from json import loads as json_loads
from .....ai import LangboardCalledVariablesComponent
from .....core.logger import Logger
from .....core.utils.Converter import json_default
from .....domain.models.BaseBotModel import BotPlatformRunningType
from ..BotTaskDataHelper import BotTaskDataHelper
from .BaseBotRequest import BaseBotRequest, RequestData


logger = Logger.use("bot-task")


class N8NRequest(BaseBotRequest):
    def create_request_data(self, bot_log):
        project_uid = self._project.get_uid() if self._project else None
        session_id = f"{self._bot.get_uid()}-{project_uid}"
        if session_id.endswith("-"):
            session_id = session_id[:-1]

        log, scope_log = bot_log

        component = LangboardCalledVariablesComponent(
            event=self._event,
            app_api_token=self._bot.app_api_token,
            project_uid=project_uid,
            current_runner_type="bot",
            current_runner_data=BotTaskDataHelper.create_user_or_bot(self._bot),
            rest_data=json_loads(
                json_dumps(
                    {key: value for key, value in self._data.items() if key != "schedule_execution"},
                    default=json_default,
                )
            ),
        )

        request_data = {
            "input_value": "",
            "run_type": "bot",
            "uid": self._bot.get_uid(),
            "project_uid": project_uid,
            "log_uid": log.get_uid(),
            "scope_log_table": scope_log.__tablename__ if scope_log else None,
            "tweaks": {
                **component.to_tweaks(),
            },
        }

        if self._bot.platform_running_type == BotPlatformRunningType.Default:
            url = self._base_url
        else:
            return None

        return RequestData(
            {
                "url": url,
                "data": request_data,
            }
        )
