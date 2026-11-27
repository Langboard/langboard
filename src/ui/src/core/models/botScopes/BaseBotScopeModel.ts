import { BaseModel, IBaseModel } from "@/core/models/Base";
import { EBotTriggerCondition } from "@/core/models/botScopes/EBotTriggerCondition";
import { Utils } from "@langboard/core/utils";

export interface Interface extends IBaseModel {
    bot_uid: string;
    default_scope_branch_uid?: string;
    conditions: EBotTriggerCondition[];
    is_frozen: bool;
}

abstract class BaseBotScopeModel<TModel extends Interface> extends BaseModel<TModel> {
    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isArray(model.conditions)) {
            model.conditions = model.conditions.map((condition) => {
                return Utils.String.convertSafeEnum(EBotTriggerCondition, condition);
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

    public get default_scope_branch_uid() {
        return this.getValue("default_scope_branch_uid");
    }
    public set default_scope_branch_uid(value) {
        this.update({ default_scope_branch_uid: value });
    }

    public get conditions() {
        return this.getValue("conditions");
    }
    public set conditions(value) {
        this.update({ conditions: value });
    }

    public get is_frozen() {
        return this.getValue("is_frozen");
    }
    public set is_frozen(value) {
        this.update({ is_frozen: value });
    }
}

export const Model = BaseBotScopeModel;
export type TModel = BaseBotScopeModel<Interface>;
