from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar


_TFiltered = TypeVar("_TFiltered")


class BaseFilter(ABC, Generic[_TFiltered]):
    _filtered: _TFiltered

    @abstractmethod
    def add(self, *args, **kwargs) -> Any:
        """Adds a data to be filtered.

        You can use as a decorator.
        """

    @abstractmethod
    def exists(self, *args, **kwargs) -> bool:
        """Checks if a data is in the filter."""
