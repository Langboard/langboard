from typing import TypeVar, cast
from ..core.db import BaseDbModel
from ..core.utils.decorators import staticclass
from ..domain import models


_TBaseModel = TypeVar("_TBaseModel", bound=BaseDbModel)


@staticclass
class ModelHelper:
    @staticmethod
    def get_model_by_table_name(table_name: str) -> type[BaseDbModel] | None:
        tables = getattr(ModelHelper.get_model_by_table_name, "__tables", {})
        setattr(ModelHelper.get_model_by_table_name, "__tables", tables)

        if table_name in tables:
            return tables[table_name]

        for model_name in models.__all__:
            model = cast(type[BaseDbModel], models.__dict__[model_name])
            if not hasattr(model, "__tablename__"):
                continue
            if model.__tablename__ == table_name:
                tables[table_name] = model
                return model
        return None

    @staticmethod
    def get_models_by_base_class(
        base_class: type[_TBaseModel],
    ) -> list[type[_TBaseModel]]:
        models_list = []
        for model_name in models.__all__:
            model = cast(type[_TBaseModel], models.__dict__[model_name])
            if not isinstance(model, type) or not issubclass(model, BaseDbModel):
                continue

            if issubclass(model, base_class):
                models_list.append(model)
        return models_list


def ensure_models_imported():
    pass
