import * as BaseBotScopeModel from "@/core/models/botScopes/BaseBotScopeModel";
import {
    CARD_CATEGORIZED_EVENT_TYPES,
    COLUMN_CATEGORIZED_EVENT_TYPES,
    DomainEventType,
} from "@/core/events/DomainEventType";
import { registerModel } from "@/core/models/ModelRegistry";

export interface Interface extends BaseBotScopeModel.Interface {
    project_column_uid: string;
}

class ProjectColumnBotScope extends BaseBotScopeModel.Model<Interface> {
    public static get MODEL_NAME() {
        return "ProjectColumnBotScope" as const;
    }

    public get project_column_uid() {
        return this.getValue("project_column_uid");
    }
    public set project_column_uid(value) {
        this.update({ project_column_uid: value });
    }
}

registerModel(ProjectColumnBotScope);

export const Model = ProjectColumnBotScope;
export type TModel = ProjectColumnBotScope;

export const CATEGORIZED_EVENT_TYPES = {
    ...COLUMN_CATEGORIZED_EVENT_TYPES,
    ...CARD_CATEGORIZED_EVENT_TYPES,
};

CATEGORIZED_EVENT_TYPES.card = [DomainEventType.CardCreated, ...CATEGORIZED_EVENT_TYPES.card];
