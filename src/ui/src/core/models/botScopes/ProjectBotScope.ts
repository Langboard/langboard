import * as BaseBotScopeModel from "@/core/models/botScopes/BaseBotScopeModel";
import {
    CARD_CATEGORIZED_EVENT_TYPES,
    COLUMN_CATEGORIZED_EVENT_TYPES,
    DomainEventType,
    PROJECT_CATEGORIZED_EVENT_TYPES,
    WIKI_CATEGORIZED_EVENT_TYPES,
} from "@/core/events/DomainEventType";
import { registerModel } from "@/core/models/ModelRegistry";

export interface Interface extends BaseBotScopeModel.Interface {
    project_uid: string;
}

class ProjectBotScope extends BaseBotScopeModel.Model<Interface> {
    public static get MODEL_NAME() {
        return "ProjectBotScope" as const;
    }

    public get project_uid() {
        return this.getValue("project_uid");
    }
    public set project_uid(value) {
        this.update({ project_uid: value });
    }
}

registerModel(ProjectBotScope);

export const Model = ProjectBotScope;
export type TModel = ProjectBotScope;

export const CATEGORIZED_EVENT_TYPES = {
    ...PROJECT_CATEGORIZED_EVENT_TYPES,
    ...WIKI_CATEGORIZED_EVENT_TYPES,
    ...COLUMN_CATEGORIZED_EVENT_TYPES,
    ...CARD_CATEGORIZED_EVENT_TYPES,
};

CATEGORIZED_EVENT_TYPES.project_column = [DomainEventType.ProjectColumnCreated, ...CATEGORIZED_EVENT_TYPES.project_column];
CATEGORIZED_EVENT_TYPES.card = [DomainEventType.CardCreated, ...CATEGORIZED_EVENT_TYPES.card];
