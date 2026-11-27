from abc import ABC, abstractmethod
from typing import Any, Callable, Literal, TypeVar
from ...infrastructure.repositories import Repository
from ..db import BaseDbModel
from ..types import IFactoryProduct
from ..utils.Converter import convert_python_data


_TService = TypeVar("_TService", bound="BaseDomainService")
TMutableValidator = Literal["default", "not_empty", "nullable"]
TMutableValidatorMap = dict[str, TMutableValidator]


class BaseDomainService(ABC, IFactoryProduct):
    @staticmethod
    @abstractmethod
    def name() -> str: ...

    def __init__(self, get_service: Callable, get_service_by_name: Callable, repository: Repository):
        self._raw_get_service = get_service
        self._raw_get_service_by_name = get_service_by_name
        self.repo = repository

    def _get_service(self, service: type[_TService]) -> _TService:
        return self._raw_get_service(service)

    def _get_service_by_name(self, name: str) -> Any:
        return self._raw_get_service_by_name(name)

    def apply_mutates(
        self, model: BaseDbModel, form: dict[str, Any], validators: TMutableValidatorMap
    ) -> dict[str, Any]:
        old_record = {}
        for key, validator in validators.items():
            if key not in form or not hasattr(model, key):
                continue
            old_value = getattr(model, key)
            new_value = form[key]
            if not self.__validate_mutate(validator, old_value, new_value):
                continue

            old_record[key] = convert_python_data(old_value)
            setattr(model, key, new_value)

        return old_record

    def __validate_mutate(self, validator: TMutableValidator, old_value: Any, new_value: Any) -> bool:
        if old_value == new_value:
            return False

        if validator == "not_empty":
            return bool(new_value)

        if validator == "nullable":
            return True

        if new_value is None:
            return False

        return True
