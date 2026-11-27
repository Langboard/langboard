from enum import Enum
from typing import Any, Callable, Generic, Protocol, TypeVar
from ..core.db.queries.Select import SelectOfScalar
from ..core.filter import BaseFilter
from ..core.utils.decorators import class_instance, thread_safe_singleton
from ..domain.models.bases import BaseRoleModel


_TMethod = TypeVar("_TMethod", bound=Callable)
_TRoleModel = TypeVar("_TRoleModel", bound=BaseRoleModel)
_TRoleModel_co = TypeVar("_TRoleModel_co", bound=BaseRoleModel)


class _RoleFinderFunc(Protocol, Generic[_TRoleModel_co]):
    def __call__(
        self, query: SelectOfScalar[_TRoleModel_co], path_params: dict[str, Any], user_id: int
    ) -> SelectOfScalar[_TRoleModel_co]: ...


@class_instance()
@thread_safe_singleton
class RoleFilter(BaseFilter[dict[_TMethod, tuple[type, list[str], _RoleFinderFunc, bool]]], Generic[_TMethod]):
    def __init__(self):
        self._filtered: dict[_TMethod, tuple[type, list[str], _RoleFinderFunc, bool]] = {}

    def add(
        self,
        role_model: type[_TRoleModel],
        actions: list[str | Enum],
        role_finder: _RoleFinderFunc,
        allowed_all_admin: bool = True,
    ) -> Callable[[_TMethod], _TMethod]:
        """Adds a method to be filtered in :class:`RoleMiddleware`.

        This will return a decorator.

        :param role_model: Role model class to be used for filtering
        :param actions: List of actions that the role can perform
        :param role_finder: Optional function to find the role in the database
        """

        def _add(method: _TMethod) -> _TMethod:
            str_actions = [action.value if isinstance(action, Enum) else action for action in actions]
            self._filtered[method] = (role_model, str_actions, role_finder, allowed_all_admin)
            return method

        return _add

    def get_filtered(self, method: _TMethod) -> tuple[type[BaseRoleModel], list[str], _RoleFinderFunc, bool]:
        """Gets the role model and actions of a method.

        :param method: Method to be filtered
        """
        return self._filtered[method]

    def exists(self, method: _TMethod) -> bool:
        """Checks if a method is in the filter.

        :param method: Method to be checked
        """
        return method in self._filtered
