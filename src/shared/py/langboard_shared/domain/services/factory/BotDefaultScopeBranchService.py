from typing import Any, Literal, cast
from ....core.domain import BaseDomainService
from ....core.domain.BaseDomainService import TMutableValidatorMap
from ....core.types import SnowflakeID
from ....core.types.BotRelatedTypes import AVAILABLE_BOT_TARGET_TABLES
from ....core.types.ParamTypes import TBotDefaultScopeBranchParam, TBotParam
from ....helpers import BotHelper, InfraHelper
from ....publishers import BotPublisher
from ...constants.DomainEventType import DomainEventType
from ...models import (
    BotDefaultScopeBranch,
    Card,
    Project,
    ProjectColumn,
)


class BotDefaultScopeBranchService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "bot_default_scope_branch"

    def get_by_id_like(self, branch: TBotDefaultScopeBranchParam | None) -> BotDefaultScopeBranch | None:
        return InfraHelper.get_by_id_like(BotDefaultScopeBranch, branch)

    def get_api_list_by_bot(self, bot: TBotParam) -> list[dict[str, Any]]:
        bot_id = InfraHelper.convert_id(bot)
        default_branches = InfraHelper.get_all_by(BotDefaultScopeBranch, "bot_id", bot_id)

        result = []
        for default_branch in default_branches:
            response = self.get_api_by_id_like_with_conditions_map(default_branch)
            if response:
                result.append(response)

        return result

    def get_api_map_by_bot_uid(self) -> dict[str, list[dict[str, Any]]]:
        default_branches = InfraHelper.get_all(BotDefaultScopeBranch)

        defaults_by_branches: dict[str, dict[SnowflakeID, list[DomainEventType]]] = {}
        for target_table in AVAILABLE_BOT_TARGET_TABLES:
            default_scope_model_cls = BotHelper.get_default_scope_model_class(target_table)
            if not default_scope_model_cls:
                continue

            all_defaults = InfraHelper.get_all(default_scope_model_cls)
            all_defaults_by_branch: dict[SnowflakeID, list[DomainEventType]] = {}
            for default_scope in all_defaults:
                if default_scope.bot_default_scope_branch_id not in all_defaults_by_branch:
                    all_defaults_by_branch[default_scope.bot_default_scope_branch_id] = []
                all_defaults_by_branch[default_scope.bot_default_scope_branch_id] = default_scope.conditions
            defaults_by_branches[target_table] = all_defaults_by_branch

        branches_by_bot: dict[str, list[dict[str, Any]]] = {}
        for default_branch in default_branches:
            conditions_map: dict[str, list[str]] = {}

            for target_table in AVAILABLE_BOT_TARGET_TABLES:
                if target_table in defaults_by_branches and default_branch.id in defaults_by_branches[target_table]:
                    conditions_map[target_table] = [
                        condition.value for condition in defaults_by_branches[target_table][default_branch.id]
                    ]

            response = default_branch.api_response(conditions_map)

            bot_uid = default_branch.bot_id.to_short_code()

            if bot_uid not in branches_by_bot:
                branches_by_bot[bot_uid] = []
            branches_by_bot[bot_uid].append(response)

        return branches_by_bot

    def get_by_id(self, id: SnowflakeID) -> BotDefaultScopeBranch | None:
        return InfraHelper.get_by(BotDefaultScopeBranch, "id", id)

    def get_api_by_id_like_with_conditions_map(
        self, branch: TBotDefaultScopeBranchParam | None
    ) -> dict[str, Any] | None:
        default_scope_branch = InfraHelper.get_by_id_like(BotDefaultScopeBranch, branch)
        if not default_scope_branch:
            return None

        conditions_map: dict[str, list[str]] = {}

        for target_table in AVAILABLE_BOT_TARGET_TABLES:
            default_scope_model_cls = BotHelper.get_default_scope_model_class(target_table)
            if not default_scope_model_cls:
                continue

            default_scope = InfraHelper.get_all_by(
                default_scope_model_cls, "bot_default_scope_branch_id", default_scope_branch.id
            )
            if default_scope:
                conditions_map[target_table] = [condition.value for condition in default_scope[0].conditions]

        response = default_scope_branch.api_response(conditions_map)
        return response

    def create(self, bot_id: SnowflakeID, name: str) -> BotDefaultScopeBranch | None:
        default_scope = BotDefaultScopeBranch(bot_id=bot_id, name=name)

        self.repo.bot_default_scope_branch.insert(default_scope)

        BotPublisher.default_scope_branch_created(default_scope)

        return default_scope

    def update(
        self, default_scope: BotDefaultScopeBranch | None, form: dict
    ) -> BotDefaultScopeBranch | Literal[True] | None:
        default_scope = InfraHelper.get_by_id_like(BotDefaultScopeBranch, default_scope)
        if not default_scope:
            return None

        validators: TMutableValidatorMap = {
            "name": "default",
        }

        old_record = self.apply_mutates(default_scope, form, validators)

        only_conditions_map_updated = not old_record and "conditions_map" in form and form["conditions_map"] is not None

        if not old_record and not only_conditions_map_updated:
            return True

        if old_record:
            self.repo.bot_default_scope_branch.update(default_scope)

        updated_conditions_map = None
        if "conditions_map" in form and form["conditions_map"] is not None:
            updated_conditions_map = self._update_branch_conditions_map(default_scope, form["conditions_map"])

        model: dict[str, Any] = {}
        for key in form:
            if key in old_record:
                model[key] = getattr(default_scope, key)

        if updated_conditions_map is not None:
            model["conditions_map"] = updated_conditions_map

        if model:
            BotPublisher.default_scope_branch_updated(default_scope.get_uid(), model)
        elif only_conditions_map_updated:
            BotPublisher.default_scope_branch_updated(
                default_scope.get_uid(), {"conditions_map": updated_conditions_map}
            )

        return default_scope

    def delete(self, uid: str | None) -> bool:
        default_scope_branch = InfraHelper.get_by_id_like(BotDefaultScopeBranch, uid)
        if not default_scope_branch:
            return False

        for target_table in AVAILABLE_BOT_TARGET_TABLES:
            bot_scope_model = BotHelper.get_bot_model_class("scope", target_table)
            if not bot_scope_model:
                continue

            bot_scopes_using_branch = InfraHelper.get_all_by(
                bot_scope_model,
                "default_scope_branch_id",
                default_scope_branch.id,
            )

            for bot_scope in bot_scopes_using_branch:
                scope_column_name = bot_scope_model.get_scope_column_name()
                default_scope_model_cls = BotHelper.get_default_scope_model_class(scope_column_name)

                if default_scope_model_cls:
                    default_scopes = InfraHelper.get_all_by(
                        default_scope_model_cls, "bot_default_scope_branch_id", default_scope_branch.id
                    )

                    if default_scopes:
                        bot_scope.conditions = default_scopes[0].conditions

                bot_scope.default_scope_branch_id = None

                repo = self._get_bot_scope_repo(target_table)
                if repo:
                    repo.update(bot_scope)

        for target_table in AVAILABLE_BOT_TARGET_TABLES:
            default_scope_model_cls = BotHelper.get_default_scope_model_class(target_table)

            if not default_scope_model_cls:
                continue

            default_scopes = InfraHelper.get_all_by(
                default_scope_model_cls, "bot_default_scope_branch_id", default_scope_branch.id
            )
            if default_scopes:
                repo = self._get_default_scope_repo(target_table)
                if repo:
                    repo.delete(cast(list[Any], default_scopes))

        self.repo.bot_default_scope_branch.delete(default_scope_branch)

        BotPublisher.default_scope_branch_deleted(default_scope_branch.get_uid())

        return True

    def _update_branch_conditions_map(
        self, default_scope_branch: BotDefaultScopeBranch, conditions_map: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        updated_conditions_map: dict[str, list[str]] = {}

        for target_table, conditions in conditions_map.items():
            if target_table not in AVAILABLE_BOT_TARGET_TABLES:
                continue

            default_scope_model_cls = BotHelper.get_default_scope_model_class(target_table)

            if not default_scope_model_cls:
                continue

            default_scopes = InfraHelper.get_all_by(
                default_scope_model_cls, "bot_default_scope_branch_id", default_scope_branch.id
            )

            condition_values: list[DomainEventType] = []
            for condition in conditions:
                try:
                    condition_values.append(DomainEventType(condition))
                except Exception:
                    try:
                        condition_values.append(DomainEventType[condition])
                    except Exception:
                        continue

            if not condition_values:
                continue

            if default_scopes:
                default_scope = default_scopes[0]
                default_scope.conditions = condition_values

                repo = self._get_default_scope_repo(target_table)
                if repo:
                    repo.update(default_scope)
            else:
                default_scope = default_scope_model_cls(
                    bot_default_scope_branch_id=default_scope_branch.id,
                    conditions=condition_values,
                )

                repo = self._get_default_scope_repo(target_table)
                if repo:
                    repo.insert(default_scope)

            updated_conditions_map[target_table] = [condition.value for condition in condition_values]

        return updated_conditions_map

    def _get_bot_scope_repo(self, target_table: str):
        if target_table == Project.__tablename__:
            return self.repo.project_bot_scope
        elif target_table == ProjectColumn.__tablename__:
            return self.repo.project_column_bot_scope
        elif target_table == Card.__tablename__:
            return self.repo.card_bot_scope
        return None

    def _get_default_scope_repo(self, target_table: str):
        if target_table == Project.__tablename__:
            return self.repo.project_bot_default_scope
        elif target_table == ProjectColumn.__tablename__:
            return self.repo.project_column_bot_default_scope
        elif target_table == Card.__tablename__:
            return self.repo.card_bot_default_scope
        return None
