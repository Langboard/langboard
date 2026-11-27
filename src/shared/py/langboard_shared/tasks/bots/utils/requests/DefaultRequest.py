from json import dumps as json_dumps
from json import loads as json_loads
from typing import Any
from .....ai import LangboardCalledAPIToolsComponent, LangboardCalledVariablesComponent
from .....ai.TweaksComponent import TweaksComponent
from .....core.logger import Logger
from .....core.utils.Converter import json_default
from .....domain.models import BotLog
from .....domain.models.BaseBotModel import BotPlatformRunningType
from .....domain.models.bases import BaseBotLogModel
from .....Env import Env
from .....helpers.ApiComfortToolHelper import create_api_comfort_tool_prompt, create_api_names_with_comfort_tools
from ..BotTaskDataHelper import BotTaskDataHelper
from .GraphRequest import GraphRequest


logger = Logger.use("bot-task")


DEFAULT_API_APPROVAL_POLICY = {
    "read": "allow",
    "create": "ask",
    "edit": "ask",
    "delete": "ask",
}


class DefaultRequest(GraphRequest):
    def create_request_data(self, bot_log: tuple[BotLog, BaseBotLogModel | None]):
        if self._bot.platform_running_type != BotPlatformRunningType.Default:
            return None

        project_uid = self._project.get_uid() if self._project else None
        session_id = f"{self._bot.get_uid()}-{project_uid}"
        if session_id.endswith("-"):
            session_id = session_id[:-1]

        log, scope_log = bot_log
        rest_data = json_loads(json_dumps(self._data, default=json_default))
        rest_data.setdefault("origin_type", self.__get_origin_type())
        rest_data.setdefault("api_approval_policy", DEFAULT_API_APPROVAL_POLICY)
        thread_id = self._create_bot_graph_thread_id(project_uid, session_id, rest_data)

        components: list[TweaksComponent] = [
            LangboardCalledVariablesComponent(
                event=self._event,
                app_api_token=self._bot.app_api_token,
                project_uid=project_uid,
                current_runner_type="bot",
                current_runner_data=BotTaskDataHelper.create_user_or_bot(self._bot),
                rest_data=rest_data,
            ),
        ]

        request_data = {
            "input_value": "",
            "input_type": "chat",
            "output_type": "chat",
            "session": session_id,
            "session_id": session_id,
            "thread_id": thread_id,
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

        request_data["tweaks"] = self.__set_default_graph_options(request_data["tweaks"])

        return self._create_graph_webhook_request_data(request_data)

    def __get_origin_type(self) -> str:
        if self._event == "bot_cron_scheduled":
            return "schedule"
        if self._event == "bot_mentioned":
            return "manual_scope_run"
        return "trigger"

    def __set_default_graph_options(self, tweaks: dict[str, Any]) -> dict[str, Any]:
        try:
            bot_value: dict = json_loads(json_dumps(json_loads(self._bot.value or "{}")))
            agent_llm = bot_value.pop("agent_llm", "")
            if not agent_llm:
                raise ValueError("agent_llm is required for Default platform")

            api_names = bot_value.pop("api_names", [])
            system_prompt = bot_value.pop("system_prompt", "")
            approval_request = bot_value.pop("approval_request", None)
            api_approval_policy = bot_value.pop("api_approval_policy", None)
            comfort_tool_names = bot_value.pop("comfort_tool_names", [])
            comfort_tool_descriptions = bot_value.pop("comfort_tool_descriptions", {})
            comfort_tool_definitions = bot_value.pop("comfort_tool_definitions", {})
            if not isinstance(api_names, list):
                api_names = []
            if not isinstance(comfort_tool_names, list):
                comfort_tool_names = []
            if not isinstance(comfort_tool_descriptions, dict):
                comfort_tool_descriptions = {}
            if not isinstance(comfort_tool_definitions, dict):
                comfort_tool_definitions = {}

            if "base_url" in bot_value and bot_value["base_url"] == "default":
                bot_value["base_url"] = Env.OLLAMA_API_URL

            comfort_tool_prompt = create_api_comfort_tool_prompt(
                comfort_tool_names, comfort_tool_descriptions, comfort_tool_definitions
            )
            if comfort_tool_prompt:
                system_prompt = "\n\n".join([prompt for prompt in [system_prompt, comfort_tool_prompt] if prompt])

            callable_api_names = create_api_names_with_comfort_tools(
                api_names, comfort_tool_names, comfort_tool_definitions
            )
            if callable_api_names:
                api_tools_component = LangboardCalledAPIToolsComponent(api_names=callable_api_names)
                tweaks["api_names"] = callable_api_names
                tweaks[LangboardCalledVariablesComponent.__name__]["api_names"] = callable_api_names
                tweaks.update(api_tools_component.to_data())
                tweaks.update(api_tools_component.to_tweaks())

            tweaks["Graph"] = {
                "agent_llm": agent_llm,
                "settings": bot_value,
                "system_prompt": system_prompt,
                "api_names": callable_api_names,
                "api_approval_policy": api_approval_policy
                if isinstance(api_approval_policy, dict)
                else DEFAULT_API_APPROVAL_POLICY,
            }
            if approval_request:
                tweaks["Graph"]["approval_request"] = approval_request
        except Exception:
            pass

        return tweaks
