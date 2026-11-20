from abc import ABC, abstractmethod
from copy import deepcopy
from enum import Enum
from typing import Any, TypedDict
from httpx import AsyncClient, RequestError, TimeoutException
from .....core.db import BaseDbModel, DbSession
from .....core.logger import Logger
from .....core.security.GraphServiceAuth import graph_service_headers
from .....core.utils.Converter import convert_python_data
from .....domain.models import Bot, BotLog, Project
from .....domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from .....domain.models.bases import BaseBotLogModel
from .....domain.models.BotLog import BotLogType
from .....Env import Env
from .....helpers import BotHelper
from .....publishers import ProjectBotPublisher
from ..BotTaskDataHelper import BotTaskDataHelper


logger = Logger.use("bot-task")


class RequestData(TypedDict, total=True):
    url: str
    data: dict[str, Any]


class BotRequestOutcome(Enum):
    """Transport admission only; Accepted does not establish execution success."""

    Accepted = "accepted"
    RetryableFailure = "retryable_failure"
    PermanentFailure = "permanent_failure"
    Skipped = "skipped"
    Uncertain = "uncertain"


class BaseBotRequest(ABC):
    def __init__(
        self,
        bot: Bot,
        base_url: str,
        event: str,
        data: dict[str, Any],
        project: Project | None,
        scope_model: BaseDbModel | None,
    ):
        self._bot = bot
        self._base_url = base_url.rstrip("/")
        self._event = event
        self._data = data
        self._project = project
        self._scope_model = scope_model

    def _can_dispatch_scope(self) -> bool:
        from ..BotTaskHelper import BotTaskHelper

        return BotTaskHelper.can_dispatch_card_scope(self._data, self._project, self._scope_model)

    async def execute(self) -> BotRequestOutcome:
        if not self._can_dispatch_scope():
            return BotRequestOutcome.Skipped
        self._data = BotTaskDataHelper.revalidate_related_cards(self._data)
        bot_log = await self._create_log(BotLogType.Info, f"'{self._event}' task started")

        request_data = self.create_request_data(bot_log)
        if not request_data:
            await self._update_log(bot_log, BotLogType.Error, "Invalid request data")
            return BotRequestOutcome.PermanentFailure

        headers = self._get_bot_request_headers()
        workflow_key = self._data.get("workflow_rule", {}).get("idempotency_key")
        if workflow_key:
            headers["Idempotency-Key"] = workflow_key

        return await self.request(request_data, headers, bot_log)

    @abstractmethod
    def create_request_data(self, bot_log: tuple[BotLog, BaseBotLogModel | None]) -> RequestData | None: ...

    async def request(
        self,
        request_data: RequestData,
        headers: dict[str, Any],
        bot_log: tuple[BotLog, BaseBotLogModel | None],
        retried: int = 0,
    ) -> BotRequestOutcome:
        if not self._can_dispatch_scope():
            return BotRequestOutcome.Skipped
        # A retry may outlive the visibility decision made when its envelope was built.
        # These are the two native component projections; do not rewrite arbitrary JSON.
        request_data = deepcopy(request_data)
        tweaks = request_data["data"].get("tweaks")
        if isinstance(tweaks, dict):
            component = tweaks.get("LangboardCalledVariablesComponent")
            for variables in (tweaks, component):
                if isinstance(variables, dict) and isinstance(variables.get("rest_data"), dict):
                    variables["rest_data"] = BotTaskDataHelper.revalidate_related_cards(variables["rest_data"])
        res = None
        scheduled = bool(
            self._data.get("schedule_execution") or self._data.get("workflow_rule", {}).get("idempotency_key")
        )
        native = self._bot.platform == BotPlatform.Default
        request_data["data"] = convert_python_data(request_data["data"], recursive=True)

        try:
            async with AsyncClient(timeout=Env.AI_REQUEST_TIMEOUT) as client:
                res = await client.post(
                    url=request_data["url"],
                    headers=headers,
                    json=request_data["data"],
                )
            res.raise_for_status()
            text = res.text
            logger.info("Successfully requested bot: %s(@%s)", self._bot.name, self._bot.bot_uname)
            log_type = self._get_start_request_log_type()
            message = "Request successfully executed"
            await self._update_log(bot_log, log_type, text if text else message)
            return BotRequestOutcome.Accepted
        except TimeoutException as e:
            if not scheduled and retried < Env.AI_REQUEST_TRIALS:
                return await self.request(request_data, headers, bot_log, retried + 1)
            logger.error("Timeout while requesting bot: %s", e)
            await self._update_log(bot_log, BotLogType.Error, str(e))
            return BotRequestOutcome.Uncertain if scheduled else BotRequestOutcome.RetryableFailure
        except RequestError as e:
            logger.error("Transport failure while requesting bot: %s", self._bot.bot_uname)
            await self._update_log(bot_log, BotLogType.Error, str(e))
            return BotRequestOutcome.Uncertain if scheduled else BotRequestOutcome.RetryableFailure
        except Exception as e:
            if res:
                logger.error(
                    "Failed to request bot: %s(@%s) %s: %s",
                    self._bot.name,
                    self._bot.bot_uname,
                    str(res.status_code),
                    res.text,
                )
                await self._update_log(bot_log, BotLogType.Error, f"{res.status_code}: {res.text}")
                if scheduled and (res.status_code in (408, 429) or res.status_code >= 500):
                    # Only native explicit pre-admission capacity rejection is safe to retry.
                    return (
                        BotRequestOutcome.RetryableFailure
                        if native and res.status_code in (429, 503)
                        else BotRequestOutcome.Uncertain
                    )
                if res.status_code in (408, 429) or res.status_code >= 500:
                    return BotRequestOutcome.RetryableFailure
            else:
                logger.error("Failed to request bot: %s(@%s)", self._bot.name, self._bot.bot_uname)
                await self._update_log(bot_log, BotLogType.Error, str(e))
            return BotRequestOutcome.PermanentFailure

    def _get_bot_request_headers(self) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self._bot.platform == BotPlatform.Default:
            headers.update(graph_service_headers())
        elif self._bot.platform == BotPlatform.Langflow:
            headers["X-API-KEY"] = self._bot.api_key
        elif self._bot.platform == BotPlatform.N8N:
            headers["Authorization"] = self._bot.api_key

        return headers

    async def _create_log(self, log_type: BotLogType, message: str):
        bot_log = BotLog(bot_id=self._bot.id, log_type=log_type)
        bot_log.append_message(message, log_type)

        with DbSession.use(readonly=False) as db:
            db.insert(bot_log)

        if not self._scope_model:
            return bot_log, None

        log_class = BotHelper.get_bot_model_class("log", self._scope_model.__tablename__)
        if not log_class:
            return bot_log, None

        params: dict[str, Any] = {
            f"{self._scope_model.__tablename__}_id": self._scope_model.id,
            "bot_log_id": bot_log.id,
        }

        scope_log = log_class(**params)
        with DbSession.use(readonly=False) as db:
            db.insert(scope_log)

        if self._project:
            ProjectBotPublisher.log_created(self._project, (bot_log, scope_log))
        return bot_log, scope_log

    async def _update_log(
        self,
        bot_log: tuple[BotLog, BaseBotLogModel | None],
        log_type: BotLogType,
        stack: str,
    ) -> None:
        log, scope_log = bot_log
        log.log_type = log_type
        log_stack = log.append_message(stack, log_type)

        with DbSession.use(readonly=False) as db:
            db.update(log)

        if self._project and scope_log:
            ProjectBotPublisher.log_stack_added(self._project, log, log_stack)

    def _get_start_request_log_type(self) -> BotLogType:
        if self._bot.platform == BotPlatform.Default:
            return BotLogType.Info
        elif self._bot.platform == BotPlatform.Langflow:
            if self._bot.platform_running_type == BotPlatformRunningType.Endpoint:
                return BotLogType.Success
        elif self._bot.platform == BotPlatform.N8N:
            if self._bot.platform_running_type == BotPlatformRunningType.Default:
                return BotLogType.Success
        return BotLogType.Success
