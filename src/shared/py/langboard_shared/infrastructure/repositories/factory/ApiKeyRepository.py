from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime
from ....core.types.ParamTypes import TUserParam
from ....domain.models import ApiKeySetting
from ....helpers import InfraHelper


class ApiKeyRepository(BaseRepository[ApiKeySetting]):
    @staticmethod
    def model_cls():
        return ApiKeySetting

    @staticmethod
    def name() -> str:
        return "api_key"

    def get_all(self) -> list[ApiKeySetting]:
        """Get all API keys"""
        results = []
        with DbSession.use(readonly=True) as db:
            results = db.exec(SqlBuilder.select.table(ApiKeySetting)).all()
        return results

    def get_all_active(self) -> list[ApiKeySetting]:
        """Get all active API keys"""
        results = []
        with DbSession.use(readonly=True) as db:
            results = db.exec(
                SqlBuilder.select.table(ApiKeySetting).where(ApiKeySetting.column("activated_at").isnot(None))
            ).all()
        return results

    def get_all_by_user(self, user_id: int) -> list[ApiKeySetting]:
        """Get API keys by user"""
        results = []
        with DbSession.use(readonly=True) as db:
            results = db.exec(
                SqlBuilder.select.table(ApiKeySetting).where(ApiKeySetting.column("user_id") == user_id)
            ).all()
        return results

    def count_api_keys_scroller(self, user: TUserParam, refer_time: SafeDateTime) -> int:
        """Count API keys created after refer_time"""
        user = InfraHelper.convert_id(user)
        outdated_query = SqlBuilder.select.count(ApiKeySetting, ApiKeySetting.column("id")).where(
            (ApiKeySetting.column("user_id") == user) & (ApiKeySetting.column("created_at") > refer_time)
        )

        count = 0
        with DbSession.use(readonly=True) as db:
            count = db.exec(outdated_query).first() or 0
        return count

    def get_api_keys_scroller(
        self, user: TUserParam, refer_time: SafeDateTime, page: int, limit: int
    ) -> list[ApiKeySetting]:
        """Get API keys created before or at refer_time, ordered by creation time (newest first)"""
        user = InfraHelper.convert_id(user)
        query = (
            SqlBuilder.select.table(ApiKeySetting)
            .where((ApiKeySetting.column("user_id") == user) & (ApiKeySetting.column("created_at") <= refer_time))
            .order_by(ApiKeySetting.column("created_at").desc(), ApiKeySetting.column("id").desc())
        )
        if limit > 0:
            query = InfraHelper.paginate(query, page, limit)

        records = []
        with DbSession.use(readonly=True) as db:
            result = db.exec(query)
            records = result.all()
        return records
