from abc import abstractmethod
from enum import Enum
from typing import Any
from ....core.db import BaseDbModel, CSVType, Field, SnowflakeIDField
from ....core.types import SnowflakeID
from ..User import User


ALL_GRANTED = "*"


class BaseRoleModel(BaseDbModel):
    actions: list[str] = Field(default=[ALL_GRANTED], sa_type=CSVType(str))
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)

    @staticmethod
    @abstractmethod
    def get_all_actions() -> list[Enum]: ...

    @staticmethod
    @abstractmethod
    def get_default_actions() -> list[Enum]: ...

    @classmethod
    def get_filterable_columns(cls) -> list[str]:
        return [field for field in cls.model_fields if field not in BaseRoleModel.model_fields]

    def is_all_granted(self) -> bool:
        if ALL_GRANTED in self.actions or self.actions == [action.value for action in self.get_all_actions()]:
            return True
        return False

    def is_granted(self, actions: Enum | str | list[Enum | str] | list[Enum] | list[str]):
        if self.is_all_granted():
            return True
        if not isinstance(actions, list):
            actions = [actions]
        actions = [action.value if isinstance(action, Enum) else action for action in actions]

        for action in actions:
            if action not in self.actions:
                return False
        return True

    def set_default_actions(self) -> None:
        if not isinstance(self, BaseRoleModel):
            return
        self.actions = [action.value for action in self.get_default_actions()]

    def set_all_actions(self) -> None:
        self.actions = [ALL_GRANTED]

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        keys: list[str | tuple[str, str]] = ["user_id", "actions"]
        keys.extend(self.get_filterable_columns())
        return keys
