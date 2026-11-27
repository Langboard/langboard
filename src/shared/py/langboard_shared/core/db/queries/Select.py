from typing import Any, Generic, Literal, Mapping, Optional, Self, Sequence, TypeGuard, TypeVar, cast, overload
from sqlalchemy import Column, func
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select as SQLAlchemySelect
from sqlalchemy.sql._typing import _DMLTableArgument
from sqlalchemy.sql.elements import ColumnElement
from ...types import SafeDateTime, SnowflakeID
from ..Models import SoftDeleteModel


_T0 = TypeVar("_T0")
_T1 = TypeVar("_T1")
_T2 = TypeVar("_T2")
_T3 = TypeVar("_T3")
_T4 = TypeVar("_T4")
_T5 = TypeVar("_T5")
_T6 = TypeVar("_T6")
_T7 = TypeVar("_T7")
_T8 = TypeVar("_T8")
_T9 = TypeVar("_T9")
_TRow = TypeVar("_TRow", bound=tuple[Any, ...])
_TSelectParam = TypeVar("_TSelectParam")
_TSelectStatement = TypeVar("_TSelectStatement", bound=SQLAlchemySelect[Any])


class SelectBase(SQLAlchemySelect[tuple[_TSelectParam]], Generic[_TSelectParam]):
    inherit_cache = True

    def where(self, *whereclause: Any) -> Self:
        return super().where(*whereclause)  # type: ignore[arg-type]

    def having(self, *having: Any) -> Self:
        return super().having(*having)  # type: ignore[arg-type]


class SelectRows(SelectBase[_TRow], Generic[_TRow]):
    inherit_cache = True


class SelectOfScalar(SelectBase[_TSelectParam], Generic[_TSelectParam]):
    inherit_cache = True


_TScalar_0 = TypeVar(
    "_TScalar_0", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_1 = TypeVar(
    "_TScalar_1", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_2 = TypeVar(
    "_TScalar_2", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_3 = TypeVar(
    "_TScalar_3", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_4 = TypeVar(
    "_TScalar_4", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_5 = TypeVar(
    "_TScalar_5", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_6 = TypeVar(
    "_TScalar_6", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_7 = TypeVar(
    "_TScalar_7", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_8 = TypeVar(
    "_TScalar_8", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)
_TScalar_9 = TypeVar(
    "_TScalar_9", Column, Sequence, Mapping, SnowflakeID, Optional[Any], SafeDateTime, float, int, bool, bytes, str
)


class SelectQuery:
    def table(self, entity: type[_T0], with_deleted: bool = False) -> SelectOfScalar[_T0]:
        statement = SelectOfScalar(entity)
        if self._is_soft_delete_model(entity, is_column=False):
            soft_delete_models: set[type[SoftDeleteModel]] = set([entity])
            statement = self._set_where(statement, soft_delete_models, with_deleted)
        return statement

    @overload
    def tables(
        self, entity0: type[_T0], entity1: type[_T1], /, with_deleted: bool = False
    ) -> SelectRows[tuple[_T0, _T1]]: ...
    @overload
    def tables(
        self, entity0: type[_T0], entity1: type[_T1], entity2: type[_T2], /, with_deleted: bool = False
    ) -> SelectRows[tuple[_T0, _T1, _T2]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        entity4: type[_T4],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3, _T4]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        entity4: type[_T4],
        entity5: type[_T5],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3, _T4, _T5]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        entity4: type[_T4],
        entity5: type[_T5],
        entity6: type[_T6],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3, _T4, _T5, _T6]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        entity4: type[_T4],
        entity5: type[_T5],
        entity6: type[_T6],
        entity7: type[_T7],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3, _T4, _T5, _T6, _T7]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        entity4: type[_T4],
        entity5: type[_T5],
        entity6: type[_T6],
        entity7: type[_T7],
        entity8: type[_T8],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3, _T4, _T5, _T6, _T7, _T8]]: ...
    @overload
    def tables(
        self,
        entity0: type[_T0],
        entity1: type[_T1],
        entity2: type[_T2],
        entity3: type[_T3],
        entity4: type[_T4],
        entity5: type[_T5],
        entity6: type[_T6],
        entity7: type[_T7],
        entity8: type[_T8],
        entity9: type[_T9],
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_T0, _T1, _T2, _T3, _T4, _T5, _T6, _T7, _T8, _T9]]: ...
    def tables(self, *entities: _DMLTableArgument, with_deleted: bool = False) -> SelectRows:  # type: ignore
        statement = SelectRows(*entities)  # type: ignore
        soft_delete_models: set[type[SoftDeleteModel]] = set(
            [entity for entity in entities if self._is_soft_delete_model(entity, is_column=False)]
        )

        return self._set_where(statement, soft_delete_models, with_deleted)

    def column(
        self, column0: InstrumentedAttribute[_TScalar_0] | ColumnElement[_TScalar_0], with_deleted: bool = False
    ) -> SelectOfScalar[_TScalar_0]:
        statement = SelectOfScalar(column0)
        if self._is_soft_delete_model(column0, is_column=True):
            soft_delete_models: set = set([column0.class_])
            statement = self._set_where(statement, soft_delete_models, with_deleted)
        return statement

    @overload
    def columns(
        self, column0: _TScalar_0, column1: _TScalar_1, /, with_deleted: bool = False
    ) -> SelectRows[tuple[_TScalar_0, _TScalar_1]]: ...
    @overload
    def columns(
        self, column0: _TScalar_0, column1: _TScalar_1, column2: _TScalar_2, /, with_deleted: bool = False
    ) -> SelectRows[tuple[_TScalar_0, _TScalar_1, _TScalar_2]]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_TScalar_0, _TScalar_1, _TScalar_2, _TScalar_3]]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        column4: _TScalar_4,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_TScalar_0, _TScalar_1, _TScalar_2, _TScalar_3, _TScalar_4]]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        column4: _TScalar_4,
        column5: _TScalar_5,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_TScalar_0, _TScalar_1, _TScalar_2, _TScalar_3, _TScalar_4, _TScalar_5]]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        column4: _TScalar_4,
        column5: _TScalar_5,
        column6: _TScalar_6,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[tuple[_TScalar_0, _TScalar_1, _TScalar_2, _TScalar_3, _TScalar_4, _TScalar_5, _TScalar_6]]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        column4: _TScalar_4,
        column5: _TScalar_5,
        column6: _TScalar_6,
        column7: _TScalar_7,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[
        tuple[_TScalar_0, _TScalar_1, _TScalar_2, _TScalar_3, _TScalar_4, _TScalar_5, _TScalar_6, _TScalar_7]
    ]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        column4: _TScalar_4,
        column5: _TScalar_5,
        column6: _TScalar_6,
        column7: _TScalar_7,
        column8: _TScalar_8,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[
        tuple[
            _TScalar_0, _TScalar_1, _TScalar_2, _TScalar_3, _TScalar_4, _TScalar_5, _TScalar_6, _TScalar_7, _TScalar_8
        ]
    ]: ...
    @overload
    def columns(
        self,
        column0: _TScalar_0,
        column1: _TScalar_1,
        column2: _TScalar_2,
        column3: _TScalar_3,
        column4: _TScalar_4,
        column5: _TScalar_5,
        column6: _TScalar_6,
        column7: _TScalar_7,
        column8: _TScalar_8,
        column9: _TScalar_9,
        /,
        with_deleted: bool = False,
    ) -> SelectRows[
        tuple[
            _TScalar_0,
            _TScalar_1,
            _TScalar_2,
            _TScalar_3,
            _TScalar_4,
            _TScalar_5,
            _TScalar_6,
            _TScalar_7,
            _TScalar_8,
            _TScalar_9,
        ]
    ]: ...
    def columns(self, *entities: _DMLTableArgument, with_deleted: bool = False) -> SelectRows:  # type: ignore
        statement = SelectRows(*entities)  # type: ignore
        soft_delete_models: set = set(
            [entity.class_ for entity in entities if self._is_soft_delete_model(entity, is_column=True)]
        )

        return self._set_where(statement, soft_delete_models, with_deleted)

    def count(
        self, entity: SQLAlchemySelect[_TRow] | type[_T0], column: InstrumentedAttribute[_T1] | ColumnElement[_T1]
    ) -> SelectOfScalar[int]:
        if isinstance(entity, SQLAlchemySelect):
            return SelectOfScalar(func.count(column)).select_from(entity.subquery())

        return SelectOfScalar(func.count(column))

    @overload
    def _is_soft_delete_model(
        self, entity: Any, is_column: Literal[True]
    ) -> TypeGuard[type[InstrumentedAttribute[SoftDeleteModel]]]: ...
    @overload
    def _is_soft_delete_model(self, entity: Any, is_column: Literal[False]) -> TypeGuard[type[SoftDeleteModel]]: ...
    def _is_soft_delete_model(self, entity: Any, is_column: bool) -> TypeGuard[type]:
        if is_column:
            return (
                isinstance(entity, InstrumentedAttribute)
                and isinstance(entity.class_, type)
                and issubclass(entity.class_, SoftDeleteModel)
            )
        return isinstance(entity, type) and issubclass(entity, SoftDeleteModel)

    def _set_where(
        self, statement: _TSelectStatement, models: set[type[SoftDeleteModel]], with_deleted: bool = False
    ) -> _TSelectStatement:
        if not with_deleted and models:
            for entity in models:
                statement = statement.where(entity.column("deleted_at").is_(None))
        return cast(_TSelectStatement, statement)
