from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, Sequence, TypeVar, overload
from ...helpers import InfraHelper
from ..db import BaseDbModel, DbSession, SqlBuilder
from ..types import IFactoryProduct
from ..types.ParamTypes import TBaseParam


_TModel = TypeVar("_TModel", bound=BaseDbModel)
_TModelParam = TypeVar("_TModelParam", bound=BaseDbModel)
_TRepository = TypeVar("_TRepository", bound="BaseRepository")


class BaseRepository(ABC, IFactoryProduct, Generic[_TModel]):
    _model_cls: type[_TModel] | None = None

    @staticmethod
    def model_cls() -> type[_TModel]:
        raise NotImplementedError("This class does not support base model operations.")

    @staticmethod
    @abstractmethod
    def name() -> str: ...

    def __init__(self, get_repository: Callable, get_repository_by_name: Callable):
        self._raw_get_repository = get_repository
        self._raw_get_repository_by_name = get_repository_by_name

    def _get_repository(self, repository: type[_TRepository]) -> _TRepository:
        return self._raw_get_repository(repository)

    def _get_repository_by_name(self, name: str) -> Any:
        return self._raw_get_repository_by_name(name)

    def insert(self, model: _TModel | _TModelParam | list[_TModel | _TModelParam]):
        with DbSession.use(readonly=False) as db:
            if isinstance(model, list):
                db.insert_all(model)
            else:
                db.insert(model)

    def update(self, model: _TModel | _TModelParam | list[_TModel | _TModelParam]):
        if not isinstance(model, list):
            model = [model]
        with DbSession.use(readonly=False) as db:
            for m in model:
                db.update(m)

    @overload
    def delete(self, models: _TModel | TBaseParam | Sequence[_TModel | TBaseParam], purge: bool = False): ...
    @overload
    def delete(
        self,
        models: _TModelParam | TBaseParam | Sequence[_TModelParam | TBaseParam],
        purge: bool = False,
        *,
        model_cls: type[_TModelParam],
    ): ...
    def delete(
        self,
        models: BaseDbModel | TBaseParam | Sequence[BaseDbModel | TBaseParam],
        purge: bool = False,
        *,
        model_cls: type[_TModelParam] | None = None,
    ):
        model = self._get_model_cls(model_cls)
        if not isinstance(models, Sequence) or isinstance(models, str):
            models = [models]
        model_ids = [InfraHelper.convert_id(m) for m in models]
        with DbSession.use(readonly=False) as db:
            db.exec(
                SqlBuilder.delete.table(model).where(model.column("id").in_(model_ids)),
                purge=purge,
            )

    def _get_model_cls(self, model_cls: type[_TModelParam] | None = None) -> type[_TModel] | type[_TModelParam]:
        if model_cls:
            return model_cls
        if not self._model_cls:
            self._model_cls = self.model_cls()
        return self._model_cls
