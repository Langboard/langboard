from typing import Callable, Generic, Literal, TypeVar
from ..utils.decorators import class_instance, thread_safe_singleton
from .BaseFilter import BaseFilter


_TMethod = TypeVar("_TMethod", bound=Callable)
_TAccessibleType = Literal["all", "user", "bot", "admin"]


@class_instance()
@thread_safe_singleton
class AuthFilter(BaseFilter[dict[_TMethod, _TAccessibleType]], Generic[_TMethod]):
    def __init__(self):
        self._filtered: dict[_TMethod, _TAccessibleType] = {}

    def add(self, accessible_type: _TAccessibleType = "all") -> Callable[[_TMethod], _TMethod]:
        """Adds a method to be filtered in :class:`ApiAuthMiddleware`.

        You can use as a decorator.

        :param method: Method to be filtered
        """

        def _add(method: _TMethod) -> _TMethod:
            self._filtered[method] = accessible_type
            return method

        return _add

    def get_filtered(self, method: _TMethod) -> _TAccessibleType:
        """Gets the role model and actions of a method.

        :param method: Method to be filtered
        """
        return self._filtered[method]

    def exists(self, method: _TMethod) -> bool:
        return method in self._filtered
