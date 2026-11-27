from typing import TypeVar
from ....core.db import BaseDbModel, DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.schema import TimeBasedPagination
from ....core.types.ParamTypes import TBaseParam, TBotParam
from ....domain.models import BotLog
from ....domain.models.bases import BaseBotLogModel
from ....helpers import InfraHelper


_TBotLogModel = TypeVar("_TBotLogModel", bound=BaseBotLogModel)


class BotLogRepository(BaseRepository[BotLog]):
    @staticmethod
    def model_cls():
        return BotLog

    @staticmethod
    def name() -> str:
        return "bot_log"

    def get_by_id_like(self, bot_log: BotLog | TBaseParam | None) -> BotLog | None:
        return InfraHelper.get_by_id_like(BotLog, bot_log)

    def get_all_by_scope(
        self,
        log_model_class: type[_TBotLogModel],
        bot: TBotParam,
        scope_model: BaseDbModel,
        pagination: TimeBasedPagination | None = None,
    ) -> list[tuple[_TBotLogModel, BotLog]]:
        bot_id = InfraHelper.convert_id(bot)
        query = (
            SqlBuilder.select.tables(log_model_class, BotLog)
            .join(BotLog, BotLog.column("id") == log_model_class.column("bot_log_id"))
            .where(
                (BotLog.column("bot_id") == bot_id)
                & (log_model_class.column(f"{scope_model.__tablename__}_id") == scope_model.id)
            )
            .order_by(log_model_class.column("updated_at").desc())
        )

        if pagination:
            query = query.where(log_model_class.column("created_at") <= pagination.refer_time)
            query = query.limit(pagination.limit).offset((pagination.page - 1) * pagination.limit)

        logs = []
        with DbSession.use(readonly=True) as db:
            result = db.exec(query)
            logs = result.all()

        return logs
