import * as BaseBotScopeModel from "@/core/models/botScopes/BaseBotScopeModel";
import { CARD_CATEGORIZED_EVENT_TYPES } from "@/core/events/DomainEventType";
import { registerModel } from "@/core/models/ModelRegistry";

export interface Interface extends BaseBotScopeModel.Interface {
    card_uid: string;
}

class ProjectCardBotScope extends BaseBotScopeModel.Model<Interface> {
    public static get MODEL_NAME() {
        return "ProjectCardBotScope" as const;
    }

    public get card_uid() {
        return this.getValue("card_uid");
    }
    public set card_uid(value) {
        this.update({ card_uid: value });
    }
}

registerModel(ProjectCardBotScope);

export const Model = ProjectCardBotScope;
export type TModel = ProjectCardBotScope;

export const CATEGORIZED_EVENT_TYPES = {
    ...CARD_CATEGORIZED_EVENT_TYPES,
};
