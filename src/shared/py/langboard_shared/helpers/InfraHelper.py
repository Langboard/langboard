from typing import Any, Literal, Sequence, TypeVar, cast, overload
from sqlalchemy import Delete, Update, func
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select
from ..core.db import BaseDbModel, DbSession, SoftDeleteModel, SqlBuilder
from ..core.types import SnowflakeID
from ..core.utils.decorators import staticclass
from .ModelHelper import ModelHelper


_TBaseModel = TypeVar("_TBaseModel", bound=BaseDbModel)
_TSelectRow = TypeVar("_TSelectRow", bound=tuple[Any, ...])
_TSelectStatement = TypeVar("_TSelectStatement", bound=Select[tuple[Any, ...]])
_TStatement = TypeVar("_TStatement", bound=Select[tuple[Any, ...]] | Update | Delete)


_TSubModel1 = TypeVar("_TSubModel1", bound=BaseDbModel)
_TSubModel2 = TypeVar("_TSubModel2", bound=BaseDbModel)
_TSubModel3 = TypeVar("_TSubModel3", bound=BaseDbModel)

_TIdParam = _TBaseModel | SnowflakeID | int | str | None


@staticclass
class InfraHelper:
    @staticmethod
    def combine_table_with_ids(
        references: list[tuple[str, SnowflakeID]] | list[tuple[str, int]],
    ) -> dict[str, set[int]]:
        table_ids_dict: dict[str, set[int]] = {}
        for table_name, record_id in references:
            if table_name not in table_ids_dict:
                table_ids_dict[table_name] = set()
            table_ids_dict[table_name].add(record_id)
        return table_ids_dict

    @overload
    @staticmethod
    def get_references(
        references: list[tuple[str, SnowflakeID]] | list[tuple[str, int]],
        as_type: Literal["api"],
        with_deleted: bool = False,
    ) -> dict[str, dict[str, Any]]: ...
    @overload
    @staticmethod
    def get_references(
        references: list[tuple[str, SnowflakeID]] | list[tuple[str, int]],
        as_type: Literal["notification"],
        with_deleted: bool = False,
    ) -> dict[str, dict[str, Any]]: ...
    @overload
    @staticmethod
    def get_references(
        references: list[tuple[str, SnowflakeID]] | list[tuple[str, int]],
        as_type: Literal["raw"],
        with_deleted: bool = False,
    ) -> dict[str, BaseDbModel]: ...
    @staticmethod
    def get_references(
        references: list[tuple[str, SnowflakeID]] | list[tuple[str, int]],
        as_type: Literal["api", "notification", "raw"],
        with_deleted: bool = False,
    ) -> dict[str, dict[str, Any]] | dict[str, BaseDbModel]:
        """Get records by table name and ids.

        Returns:
            dict[str, dict[str, Any]]: "tablename_id" pattern
            dict[str, dict[str, Any]]: {
                "tablename_id": {
                    "id": 1,
                    "name": "test",
                    ...
                }
            }
        """
        table_ids_dict = InfraHelper.combine_table_with_ids(references)

        cached_dict = {}
        for table_name, record_ids in table_ids_dict.items():
            records = InfraHelper.get_records_by_table_name_with_ids(table_name, record_ids, with_deleted)
            if not records:
                continue

            for record in records:
                cache_key = f"{table_name}_{record.id}"
                if cache_key in cached_dict:
                    continue
                if as_type == "raw":
                    cached_dict[cache_key] = record
                elif as_type == "api":
                    cached_dict[cache_key] = record.api_response()
                elif as_type == "notification":
                    cached_dict[cache_key] = record.notification_data()
        return cached_dict

    @staticmethod
    def get_records_by_table_name_with_ids(
        table_name: str,
        record_ids: list[SnowflakeID | int] | set[SnowflakeID | int],
        with_deleted: bool = False,
    ) -> Sequence[BaseDbModel] | None:
        table = ModelHelper.get_model_by_table_name(table_name)
        if not table:
            return None

        return InfraHelper.get_all_by(table, "id", record_ids, with_deleted)

    @staticmethod
    def paginate(statement: _TSelectStatement, page: int, limit: int) -> _TSelectStatement:
        return cast(_TSelectStatement, statement.limit(limit).offset((page - 1) * limit))

    @staticmethod
    def set_order_in_column(query: Update, model_class: type[_TBaseModel], original_order: int, order: int) -> Update:
        if original_order < order:
            query = query.values({model_class.column("order"): model_class.column("order") - 1}).where(
                (model_class.column("order") <= order) & (model_class.column("order") > original_order)
            )
        else:
            query = query.values({model_class.column("order"): model_class.column("order") + 1}).where(
                (model_class.column("order") >= order) & (model_class.column("order") < original_order)
            )
        return query

    @overload
    @staticmethod
    def where_recursive(
        statement: _TSelectStatement, model_class: type[_TBaseModel], **kwargs: Any
    ) -> _TSelectStatement: ...
    @overload
    @staticmethod
    def where_recursive(statement: Update, model_class: type[_TBaseModel], **kwargs: Any) -> Update: ...
    @overload
    @staticmethod
    def where_recursive(statement: Delete, model_class: type[_TBaseModel], **kwargs: Any) -> Delete: ...
    @staticmethod
    def where_recursive(statement: _TStatement, model_class: type[_TBaseModel], **kwargs: Any) -> _TStatement:
        if not kwargs:
            return statement
        arg, value = kwargs.popitem()
        if arg in model_class.model_fields and value is not None:
            if not isinstance(arg, InstrumentedAttribute):
                arg = model_class.column(arg)
            if isinstance(value, list):
                statement = statement.where(arg.in_(value))
            else:
                statement = statement.where(arg == value)
        return InfraHelper.where_recursive(statement, model_class, **kwargs)

    @staticmethod
    def get_next_order(
        model_class: type[_TBaseModel],
        column: str,
        value: Any,
        where_clauses: dict[str, Any] | None = None,
    ) -> int:
        query = (
            SqlBuilder.select.columns(
                func.sum(model_class.column("order")),
                func.count(model_class.column("id")),
            )
            .where(model_class.column(column) == value)
            .group_by(model_class.column(column))
            .limit(1)
        )
        if issubclass(model_class, SoftDeleteModel):
            query = query.where(model_class.column("deleted_at") == None)  # noqa
        if where_clauses:
            query = InfraHelper.where_recursive(query, model_class, **where_clauses)
        sum_all, count_all = None, None
        with DbSession.use(readonly=True) as target_db:
            result = target_db.exec(query)
            sum_all, count_all = result.first() or (None, None)

        if sum_all is None or count_all is None:
            sum_all = 0
            count_all = 0

        expected_sum = (count_all * (count_all - 1)) // 2
        if sum_all != expected_sum:
            new_order_cte = SqlBuilder.select.columns(
                model_class.column("id"),
                (func.row_number().over(order_by=model_class.column("order")) - 1).label("new_order"),
            ).where(model_class.column(column) == value)
            if where_clauses:
                new_order_cte = InfraHelper.where_recursive(new_order_cte, model_class, **where_clauses)
            new_order_cte = new_order_cte.cte("new_order_cte")

            update_query = (
                SqlBuilder.update.table(model_class)
                .filter(model_class.column("id") == new_order_cte.c.id)
                .values({model_class.column("order"): new_order_cte.c.new_order})
            )

            with DbSession.use(readonly=False) as target_db:
                target_db.exec(update_query)

        return count_all

    @staticmethod
    def get_by(
        model_class: type[_TBaseModel],
        column: str,
        value: Any,
        is_none: bool = False,
        with_deleted: bool = False,
    ) -> _TBaseModel | None:
        if not is_none and value is None:
            return None
        result = None
        with DbSession.use(readonly=True) as target_db:
            result = target_db.exec(
                SqlBuilder.select.table(model_class, with_deleted=with_deleted)
                .where(model_class.column(column) == value)
                .limit(1)
            )
        if not result:
            return None
        return result.first()

    @staticmethod
    def get_all(model_class: type[_TBaseModel], with_deleted: bool = False) -> list[_TBaseModel]:
        records = []
        with DbSession.use(readonly=True) as target_db:
            result = target_db.exec(SqlBuilder.select.table(model_class, with_deleted=with_deleted))
            records = result.all()
        return records

    @staticmethod
    def get_by_id_like(
        model_class: type[_TBaseModel], id_like: _TIdParam, with_deleted: bool = False
    ) -> _TBaseModel | None:
        if not id_like:
            return None
        if isinstance(id_like, model_class):
            return id_like
        if isinstance(id_like, SnowflakeID) or isinstance(id_like, int):
            return InfraHelper.get_by(model_class, "id", id_like, with_deleted=with_deleted)
        if isinstance(id_like, str):
            return InfraHelper.get_by(
                model_class,
                "id",
                SnowflakeID.from_short_code(id_like),
                with_deleted=with_deleted,
            )
        return None

    @staticmethod
    def convert_id(id_like: BaseDbModel | SnowflakeID | int | str) -> SnowflakeID:
        if isinstance(id_like, BaseDbModel):
            return id_like.id
        if isinstance(id_like, SnowflakeID):
            return id_like
        if isinstance(id_like, int):
            return SnowflakeID(id_like)
        if isinstance(id_like, str):
            return SnowflakeID.from_short_code(id_like)
        return SnowflakeID(0)

    @staticmethod
    def convert_uid(id_like: BaseDbModel | SnowflakeID | int | str) -> str:
        if isinstance(id_like, BaseDbModel):
            return id_like.get_uid()
        if isinstance(id_like, SnowflakeID):
            return id_like.to_short_code()
        if isinstance(id_like, int):
            return SnowflakeID(id_like).to_short_code()
        if isinstance(id_like, str):
            return id_like
        return SnowflakeID(0).to_short_code()

    @staticmethod
    def get_all_by(
        model_class: type[_TBaseModel],
        column: str,
        values: Any | list[Any],
        with_deleted: bool = False,
    ) -> list[_TBaseModel]:
        sql_query = SqlBuilder.select.table(model_class, with_deleted=with_deleted)
        if not isinstance(values, list) and not isinstance(values, set):
            values = [values]
        sql_query = sql_query.where(model_class.column(column).in_(values))
        records = []
        with DbSession.use(readonly=True) as target_db:
            result = target_db.exec(sql_query)
            records = result.all()
        return records

    @overload
    @staticmethod
    def get_records_with_foreign_by_params(
        main: tuple[type[_TBaseModel], _TIdParam],
    ) -> _TBaseModel | None: ...
    @overload
    @staticmethod
    def get_records_with_foreign_by_params(
        main: tuple[type[_TBaseModel], _TIdParam],
        sub1: tuple[type[_TSubModel1], _TIdParam],
    ) -> tuple[_TBaseModel, _TSubModel1] | None: ...
    @overload
    @staticmethod
    def get_records_with_foreign_by_params(
        main: tuple[type[_TBaseModel], _TIdParam],
        sub1: tuple[type[_TSubModel1], _TIdParam],
        sub2: tuple[type[_TSubModel2], _TIdParam],
    ) -> tuple[_TBaseModel, _TSubModel1, _TSubModel2] | None: ...
    @overload
    @staticmethod
    def get_records_with_foreign_by_params(
        main: tuple[type[_TBaseModel], _TIdParam],
        sub1: tuple[type[_TSubModel1], _TIdParam],
        sub2: tuple[type[_TSubModel2], _TIdParam],
        sub3: tuple[type[_TSubModel3], _TIdParam],
    ) -> tuple[_TBaseModel, _TSubModel1, _TSubModel2, _TSubModel3] | None: ...
    @staticmethod
    def get_records_with_foreign_by_params(  # type: ignore
        main: tuple[type[BaseDbModel], _TIdParam],
        *subs: tuple[type[BaseDbModel], _TIdParam],
    ) -> Any | None:
        main_model, main_id = main
        if isinstance(main_id, str):
            main_id = SnowflakeID.from_short_code(main_id)
        elif not main_id:
            return None

        tables = [main]
        table_id_pairs: list[tuple[type[BaseDbModel], SnowflakeID]] = []
        models: list[BaseDbModel] = []

        if not subs:
            if isinstance(main_id, main_model):
                return main_id
            query = SqlBuilder.select.table(main_model).where(main_model.column("id") == main_id)
        else:
            tables.extend(subs)
            for table, table_id in tables:
                if not table_id:
                    return None

                if isinstance(table_id, table):
                    models.append(table_id)
                    table_id_pairs.append((table, table_id.id))
                elif isinstance(table_id, int) or isinstance(table_id, SnowflakeID):
                    table_id_pairs.append((table, cast(SnowflakeID, table_id)))
                elif isinstance(table_id, str):
                    table_id_pairs.append((table, SnowflakeID.from_short_code(table_id)))
                else:
                    return None

            if len(models) == len(tables):
                return tuple(models)

            table_id_pairs = table_id_pairs[::-1]

            query = SqlBuilder.select.tables(*[cast(Any, table) for table, _ in table_id_pairs]).where(
                table_id_pairs[0][0].column("id") == table_id_pairs[0][1]
            )

            for i in range(1, len(table_id_pairs)):
                table, table_id = table_id_pairs[i]
                prev_table, _ = table_id_pairs[i - 1]
                prev_table_foreigns = prev_table.get_foreign_models(opposite=True)

                if table not in prev_table_foreigns:
                    return None

                on_clause = None
                for foreign_key in prev_table_foreigns[table]:
                    if on_clause is None:
                        on_clause = prev_table.column(foreign_key) == table.column("id")
                    else:
                        on_clause = on_clause | (prev_table.column(foreign_key) == table.column("id"))

                if on_clause is None:
                    return None

                query = query.join(table, on_clause).where(table.column("id") == table_id)

        query = query.limit(1)
        records = None
        with DbSession.use(readonly=True) as target_db:
            result = target_db.exec(query)
            records = result.first()
        if not records:
            return None

        if isinstance(records, BaseDbModel):
            records = (records,)

        if len(records) != len(tables):
            return None

        return tuple(list(records)[::-1])
