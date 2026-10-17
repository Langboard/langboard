from typing import Literal
from sqlalchemy import func, select
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types.ParamTypes import TCheckitemParam
from ....domain.models import Checkitem, CheckitemTimerRecord
from ....helpers import InfraHelper


class CheckitemTimerRecordRepository(BaseRepository[CheckitemTimerRecord]):
    @staticmethod
    def model_cls():
        return CheckitemTimerRecord

    @staticmethod
    def name() -> str:
        return "checkitem_timer_record"

    def get_by_checkitem_and_arc_type(self, checkitem: TCheckitemParam, arc_type: Literal["first", "last"]):
        checkitem_id = InfraHelper.convert_id(checkitem)
        order_by = (
            (CheckitemTimerRecord.column("created_at").asc(), CheckitemTimerRecord.column("id").asc())
            if arc_type == "first"
            else (CheckitemTimerRecord.column("created_at").desc(), CheckitemTimerRecord.column("id").desc())
        )

        record = None
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                SqlBuilder.select.table(CheckitemTimerRecord)
                .where(CheckitemTimerRecord.column("checkitem_id") == checkitem_id)
                .order_by(*order_by)
                .limit(1)
            )
            record = result.first()
        return record

    def get_arc_map_by_checkitems(
        self, checkitems: list[Checkitem]
    ) -> dict[int, dict[Literal["first", "last"], CheckitemTimerRecord]]:
        checkitem_ids = [checkitem.id for checkitem in checkitems]
        if not checkitem_ids:
            return {}

        # One snapshot and one sort per item. Only the two endpoint rows leave
        # the database; long timer histories are never hydrated in Python.
        ranked = (
            SqlBuilder.select.columns(
                CheckitemTimerRecord.column("id"),
                func.row_number()
                .over(
                    partition_by=CheckitemTimerRecord.column("checkitem_id"),
                    order_by=(CheckitemTimerRecord.column("created_at"), CheckitemTimerRecord.column("id")),
                )
                .label("position"),
                func.count().over(partition_by=CheckitemTimerRecord.column("checkitem_id")).label("total"),
            )
            .where(CheckitemTimerRecord.column("checkitem_id").in_(checkitem_ids))
            .subquery()
        )
        query = (
            select(CheckitemTimerRecord, ranked.c.position, ranked.c.total)
            .join(ranked, ranked.c.id == CheckitemTimerRecord.column("id"))
            .where((ranked.c.position == 1) | (ranked.c.position == ranked.c.total))
        )
        with DbSession.use(readonly=True) as db:
            records = db.exec(query).all()

        result: dict[int, dict[Literal["first", "last"], CheckitemTimerRecord]] = {}
        for timer, position, total in records:
            arc = result.setdefault(timer.checkitem_id, {})
            if position == 1:
                arc["first"] = timer
            if position == total:
                arc["last"] = timer
        return result
