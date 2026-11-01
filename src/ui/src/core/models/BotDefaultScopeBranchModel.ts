import { BaseModel, IBaseModel } from "@/core/models/Base";
import { registerModel } from "@/core/models/ModelRegistry";
import useBotDefaultScopeBranchUpdatedHandlers from "@/controllers/socket/global/useBotDefaultScopeBranchUpdatedHandlers";
import useBotDefaultScopeBranchDeletedHandlers from "@/controllers/socket/global/useBotDefaultScopeBranchDeletedHandlers";
import { DomainEventType } from "@/core/events/DomainEventType";
import { Utils } from "@langboard/core/utils";
import { TBotRelatedTargetTable } from "@/core/models/types/bot.related.type";

export interface Interface extends IBaseModel {
    bot_uid: string;
    name: string;
    conditions_map?: Record<TBotRelatedTargetTable, DomainEventType[]>;
}

class BotDefaultScopeBranchModel extends BaseModel<Interface> {
    public static get MODEL_NAME() {
        return "BotDefaultScopeBranchModel" as const;
    }

    constructor(model: Record<string, unknown>) {
        super(model);

        this.subscribeSocketEvents([useBotDefaultScopeBranchUpdatedHandlers, useBotDefaultScopeBranchDeletedHandlers], {
            branch: this,
        });
    }

    public static convertModel(model: Interface): Interface {
        if (model.conditions_map) {
            Object.entries(model.conditions_map).forEach(([key, conditions]) => {
                model.conditions_map![key] = conditions.map((condition) => Utils.String.convertSafeEnum(DomainEventType, condition));
            });
        }

        return model;
    }

    public get bot_uid() {
        return this.getValue("bot_uid");
    }
    public set bot_uid(value) {
        this.update({ bot_uid: value });
    }

    public get name() {
        return this.getValue("name");
    }
    public set name(value) {
        this.update({ name: value });
    }

    public get conditions_map() {
        return this.getValue("conditions_map");
    }
    public set conditions_map(value) {
        this.update({ conditions_map: value });
    }
}

registerModel(BotDefaultScopeBranchModel);

export const Model = BotDefaultScopeBranchModel;
export type TModel = BotDefaultScopeBranchModel;
