from typing import TypeVar
from ....core.db import BaseDbModel
from ....core.domain import BaseDomainService
from ....core.types.ParamTypes import TBaseParam, TUserOrBot
from ...models.bases import BaseReactionModel


_TReactionModel = TypeVar("_TReactionModel", bound=BaseReactionModel)


class ReactionService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "reaction"

    def get_api_map(
        self, model_cls: type[BaseReactionModel], targets: BaseDbModel | TBaseParam | list[BaseDbModel | TBaseParam]
    ) -> dict[int, dict[str, list[str]]]:
        records = self.repo.reaction.get_all(model_cls, targets)

        reactions: dict[int, dict[str, list[str]]] = {}
        for reaction, reacted_user, reacted_bot in records:
            reaction_type = reaction.reaction_type
            reaction_target_column_name = model_cls.get_target_column_name()
            reaction_target_id = getattr(reaction, reaction_target_column_name)
            if reaction_target_id not in reactions:
                reactions[reaction_target_id] = {}
            if reaction_type not in reactions[reaction_target_id]:
                reactions[reaction_target_id][reaction_type] = []
            reactions[reaction_target_id][reaction_type].append(
                reacted_user.get_uid() if reacted_user else reacted_bot.get_uid()
            )

        return reactions

    def toggle(
        self,
        user_or_bot: TUserOrBot,
        model_cls: type[_TReactionModel],
        target: BaseDbModel | TBaseParam,
        reaction_type: str,
    ) -> bool:
        reaction = self.repo.reaction.get_one(user_or_bot, model_cls, target, reaction_type)

        if reaction:
            self.repo.reaction.toggle(user_or_bot, model_cls, target, False, reaction_type, reaction)
        else:
            self.repo.reaction.toggle(user_or_bot, model_cls, target, True, reaction_type)

        return not bool(reaction)
