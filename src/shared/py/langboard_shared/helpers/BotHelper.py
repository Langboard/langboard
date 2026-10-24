from typing import Any, ClassVar, Literal, cast, overload
from ..core.types.BotRelatedTypes import AVAILABLE_BOT_TARGET_TABLES, TBotAvailableTargets, TBotTypeName
from ..core.utils.decorators import staticclass
from ..domain.constants.DomainEventType import DomainEventType
from ..domain.models import (
    Card,
    CardBotDefaultScope,
    Project,
    ProjectBotDefaultScope,
    ProjectColumn,
    ProjectColumnBotDefaultScope,
)
from ..domain.models.bases import BaseBotDefaultScope, BaseBotLogModel, BaseBotScheduleModel, BaseBotScopeModel
from .InfraHelper import InfraHelper
from .ModelHelper import ModelHelper


_TBaseBotType = BaseBotScheduleModel | BaseBotScopeModel | BaseBotLogModel
_TBaseBotTypeClass = type[BaseBotScheduleModel] | type[BaseBotScopeModel] | type[BaseBotLogModel]


@staticclass
class BotHelper:
    AVAILABLE_BOT_TABLES: ClassVar[dict[TBotTypeName, _TBaseBotTypeClass]] = {
        "schedule": BaseBotScheduleModel,
        "scope": BaseBotScopeModel,
        "log": BaseBotLogModel,
    }
    SUFFIXES: ClassVar[dict[TBotTypeName, str]] = {
        "schedule": "bot_schedule",
        "scope": "bot_scope",
        "log": "bot_log",
    }

    @overload
    @staticmethod
    def get_bot_model_class(
        class_type: Literal["schedule"], target_table: str
    ) -> type[BaseBotScheduleModel] | None: ...
    @overload
    @staticmethod
    def get_bot_model_class(class_type: Literal["scope"], target_table: str) -> type[BaseBotScopeModel] | None: ...
    @overload
    @staticmethod
    def get_bot_model_class(class_type: Literal["log"], target_table: str) -> type[BaseBotLogModel] | None: ...
    @staticmethod
    def get_bot_model_class(class_type: TBotTypeName, target_table: str) -> _TBaseBotTypeClass | None:
        if target_table not in AVAILABLE_BOT_TARGET_TABLES:
            return None

        base_model_class = BotHelper.AVAILABLE_BOT_TABLES.get(class_type)
        if not base_model_class:
            return None

        model_classes = ModelHelper.get_models_by_base_class(base_model_class)
        if not model_classes:
            return None

        for model_class in model_classes:
            if model_class.__tablename__.startswith(target_table):
                return model_class

        return None

    @overload
    @staticmethod
    def get_target_model_by_param(
        class_type: Literal["schedule"], target_table: str, param: int | str
    ) -> tuple[type[BaseBotScheduleModel], TBotAvailableTargets] | None: ...
    @overload
    @staticmethod
    def get_target_model_by_param(
        class_type: Literal["scope"], target_table: str, param: int | str
    ) -> tuple[type[BaseBotScopeModel], TBotAvailableTargets] | None: ...
    @overload
    @staticmethod
    def get_target_model_by_param(
        class_type: Literal["log"], target_table: str, param: int | str
    ) -> tuple[type[BaseBotLogModel], TBotAvailableTargets] | None: ...
    @staticmethod
    def get_target_model_by_param(
        class_type: TBotTypeName, target_table: str, param: int | str
    ) -> tuple[_TBaseBotTypeClass, TBotAvailableTargets] | None:
        if target_table not in AVAILABLE_BOT_TARGET_TABLES:
            return None

        target_schedule_class = BotHelper.get_bot_model_class(class_type, target_table)
        target_class = AVAILABLE_BOT_TARGET_TABLES.get(target_table)
        if not target_class or not target_schedule_class:
            return None

        target = InfraHelper.get_by_id_like(target_class, param)
        if not target:
            return None

        return target_schedule_class, target

    @overload
    @staticmethod
    def get_target_model_by_bot_model(
        class_type: Literal["schedule"], bot_model: BaseBotScheduleModel
    ) -> TBotAvailableTargets | None: ...
    @overload
    @staticmethod
    def get_target_model_by_bot_model(
        class_type: Literal["scope"], bot_model: BaseBotScopeModel
    ) -> TBotAvailableTargets | None: ...
    @overload
    @staticmethod
    def get_target_model_by_bot_model(
        class_type: Literal["log"], bot_model: BaseBotLogModel
    ) -> TBotAvailableTargets | None: ...
    @staticmethod
    def get_target_model_by_bot_model(
        class_type: TBotTypeName, bot_model: _TBaseBotType
    ) -> TBotAvailableTargets | None:
        if not bot_model:
            return None

        target_table = BotHelper.get_target_table_by_bot_model(class_type, bot_model.__class__)
        if not target_table:
            return None

        target_class = AVAILABLE_BOT_TARGET_TABLES.get(target_table)
        if not target_class:
            return None

        target = InfraHelper.get_by_id_like(target_class, getattr(bot_model, f"{target_table}_id"))
        if not target:
            return None

        return target

    @staticmethod
    def get_scope_model_classes_by_condition(condition: DomainEventType):
        model_classes = ModelHelper.get_models_by_base_class(BaseBotScopeModel)
        available_classes: set[type[BaseBotScopeModel]] = set()
        for model_class in model_classes:
            if condition in cast(Any, model_class).get_available_conditions():
                available_classes.add(model_class)
        return available_classes

    @staticmethod
    def get_target_table_by_bot_model(class_type: TBotTypeName, bot_model: _TBaseBotTypeClass) -> str | None:
        if not bot_model:
            return None

        target_table = bot_model.__tablename__.split(f"_{BotHelper.SUFFIXES[class_type]}")[0]
        if target_table not in AVAILABLE_BOT_TARGET_TABLES:
            return None

        return target_table

    @staticmethod
    def get_default_scope_model_class(name: str) -> type[BaseBotDefaultScope] | None:
        if name in {Project.__tablename__, "project_id"}:
            return ProjectBotDefaultScope
        elif name in {ProjectColumn.__tablename__, "project_column_id"}:
            return ProjectColumnBotDefaultScope
        elif name in {Card.__tablename__, "card_id"}:
            return CardBotDefaultScope
        return None
