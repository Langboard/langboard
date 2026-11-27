/* eslint-disable @typescript-eslint/no-explicit-any */
import type { Model as ActivityModel } from "@/core/models/ActivityModel";
import type { Model as ApiComfortToolModel } from "@/core/models/ApiComfortToolModel";
import type { Model as ApiKeySettingModel } from "@/core/models/ApiKeySettingModel";
import type { Model as AuthUserModel } from "@/core/models/AuthUser";
import type { Model as BotDefaultScopeBranchModel } from "@/core/models/BotDefaultScopeBranchModel";
import type { Model as BotLogModel } from "@/core/models/BotLogModel";
import type { Model as BotModel } from "@/core/models/BotModel";
import type { Model as ChatMessageModel } from "@/core/models/ChatMessageModel";
import type { Model as ChatSessionModel } from "@/core/models/ChatSessionModel";
import type { Model as ChatTemplateModel } from "@/core/models/ChatTemplateModel";
import type { Model as GlobalRelationshipTypeModel } from "@/core/models/GlobalRelationshipType";
import type { Model as GraphApprovalRequestModel } from "@/core/models/GraphApprovalRequestModel";
import type { Model as InternalBotModel } from "@/core/models/InternalBotModel";
import type { Model as McpToolGroupModel } from "@/core/models/McpToolGroup";
import type { Model as MetadataModel } from "@/core/models/MetadataModel";
import type { Model as NotificationScheduleRuleModel } from "@/core/models/NotificationScheduleRuleModel";
import type { Model as ProjectModel } from "@/core/models/Project";
import type { Model as ProjectCardModel } from "@/core/models/ProjectCard";
import type { Model as ProjectCardAttachmentModel } from "@/core/models/ProjectCardAttachment";
import type { Model as ProjectCardCommentModel } from "@/core/models/ProjectCardComment";
import type { Model as ProjectCardRelationshipModel } from "@/core/models/ProjectCardRelationship";
import type { Model as ProjectChecklistModel } from "@/core/models/ProjectChecklist";
import type { Model as ProjectCheckitemModel } from "@/core/models/ProjectCheckitem";
import type { Model as ProjectColumnModel } from "@/core/models/ProjectColumn";
import type { Model as ProjectLabelModel } from "@/core/models/ProjectLabel";
import type { Model as ProjectWikiModel } from "@/core/models/ProjectWiki";
import type { Model as UserModel } from "@/core/models/User";
import type { Model as UserGroupModel } from "@/core/models/UserGroup";
import type { Model as UserNotificationModel } from "@/core/models/UserNotification";
import type { Model as WebhookModel } from "@/core/models/WebhookModel";
import type { Interface as BaseBotScopeInterface } from "@/core/models/botScopes/BaseBotScopeModel";
import type { Model as ProjectBotScopeModel } from "@/core/models/botScopes/ProjectBotScope";
import type { Model as ProjectColumnBotScopeModel } from "@/core/models/botScopes/ProjectColumnBotScope";
import type { Model as ProjectCardBotScopeModel } from "@/core/models/botScopes/ProjectCardBotScope";
import type { Interface as BaseBotScheduleInterface } from "@/core/models/botSchedules/BaseBotScheduleModel";
import type { Model as ProjectBotScheduleModel } from "@/core/models/botSchedules/ProjectBotSchedule";
import type { Model as ProjectColumnBotScheduleModel } from "@/core/models/botSchedules/ProjectColumnBotSchedule";
import type { Model as ProjectCardBotScheduleModel } from "@/core/models/botSchedules/ProjectCardBotSchedule";
import { createContext, useContext } from "react";

export interface IModelMap {
    ActivityModel: IModelRegistry<typeof ActivityModel>;
    ApiComfortToolModel: IModelRegistry<typeof ApiComfortToolModel>;
    ApiKeySettingModel: IModelRegistry<typeof ApiKeySettingModel>;
    AuthUser: IModelRegistry<typeof AuthUserModel>;
    BotDefaultScopeBranchModel: IModelRegistry<typeof BotDefaultScopeBranchModel>;
    BotLogModel: IModelRegistry<typeof BotLogModel>;
    BotModel: IModelRegistry<typeof BotModel>;
    ChatMessageModel: IModelRegistry<typeof ChatMessageModel>;
    ChatSessionModel: IModelRegistry<typeof ChatSessionModel>;
    ChatTemplateModel: IModelRegistry<typeof ChatTemplateModel>;
    GlobalRelationshipType: IModelRegistry<typeof GlobalRelationshipTypeModel>;
    GraphApprovalRequestModel: IModelRegistry<typeof GraphApprovalRequestModel>;
    InternalBotModel: IModelRegistry<typeof InternalBotModel>;
    McpToolGroup: IModelRegistry<typeof McpToolGroupModel>;
    MetadataModel: IModelRegistry<typeof MetadataModel>;
    NotificationScheduleRuleModel: IModelRegistry<typeof NotificationScheduleRuleModel>;
    Project: IModelRegistry<typeof ProjectModel>;
    ProjectCard: IModelRegistry<typeof ProjectCardModel>;
    ProjectCardAttachment: IModelRegistry<typeof ProjectCardAttachmentModel>;
    ProjectCardComment: IModelRegistry<typeof ProjectCardCommentModel>;
    ProjectCardRelationship: IModelRegistry<typeof ProjectCardRelationshipModel>;
    ProjectChecklist: IModelRegistry<typeof ProjectChecklistModel>;
    ProjectCheckitem: IModelRegistry<typeof ProjectCheckitemModel>;
    ProjectColumn: IModelRegistry<typeof ProjectColumnModel>;
    ProjectLabel: IModelRegistry<typeof ProjectLabelModel>;
    ProjectWiki: IModelRegistry<typeof ProjectWikiModel>;
    User: IModelRegistry<typeof UserModel>;
    UserGroup: IModelRegistry<typeof UserGroupModel>;
    UserNotification: IModelRegistry<typeof UserNotificationModel>;
    WebhookModel: IModelRegistry<typeof WebhookModel>;

    ProjectBotScope: IModelRegistry<typeof ProjectBotScopeModel>;
    ProjectColumnBotScope: IModelRegistry<typeof ProjectColumnBotScopeModel>;
    ProjectCardBotScope: IModelRegistry<typeof ProjectCardBotScopeModel>;
    ProjectBotSchedule: IModelRegistry<typeof ProjectBotScheduleModel>;
    ProjectColumnBotSchedule: IModelRegistry<typeof ProjectColumnBotScheduleModel>;
    ProjectCardBotSchedule: IModelRegistry<typeof ProjectCardBotScheduleModel>;
}

export type TPickedModelClass<TModelName extends keyof IModelMap> = IModelMap[TModelName]["Model"];
export type TPickedModel<TModelName extends keyof IModelMap> = InstanceType<TPickedModelClass<TModelName>>;
export type TOrderableModelName = {
    [TKey in keyof IModelMap]: TPickedModel<TKey> extends { order: number } ? TKey : never;
}[keyof IModelMap];
export type TOrderableModel<TModelName extends TOrderableModelName> = TPickedModel<TModelName>;
export type TCreatedAtModelName = {
    [TKey in keyof IModelMap]: TPickedModel<TKey> extends { created_at: Date } ? TKey : never;
}[keyof IModelMap];
export type TCreatedAtModel<TModelName extends TCreatedAtModelName> = TPickedModel<TModelName>;

export type TBotScopeModelName = {
    [TKey in keyof IModelMap]: TPickedModel<TKey> extends BaseBotScopeInterface ? TKey : never;
}[keyof IModelMap];
export type TBotScopeModel<TModelName extends TBotScopeModelName> = TPickedModel<TModelName>;

export type TBotScheduleModelName = {
    [TKey in keyof IModelMap]: TPickedModel<TKey> extends BaseBotScheduleInterface ? TKey : never;
}[keyof IModelMap];
export type TBotScheduleModel<TModelName extends TBotScheduleModelName> = TPickedModel<TModelName>;

export type TUserLikeModelName = "AuthUser" | "User" | "BotModel";
export type TUserLikeModel = TPickedModel<TUserLikeModelName>;

type TClass = abstract new (...args: any) => any;
type TModelInstance<TModel extends TClass> = InstanceType<TModel>;

interface IModelRegistry<TModel extends TClass, TRegistryParams = any> {
    Model: TModel;
    Provider: React.ComponentType<IModelProviderProps<TModel, TRegistryParams>>;
    Context: React.Context<IModelContext<TModel, TRegistryParams> | undefined>;
    useContext: <TParams = any>() => IModelContext<TModel, TParams>;
}

export interface IModelContext<TModel extends TClass, TParams = any> {
    model: TModelInstance<TModel>;
    params: TParams;
}

interface IModelProviderProps<TModel extends TClass, TParams = any> {
    model: TModelInstance<TModel>;
    params?: TParams;
    children: React.ReactNode;
}

export const ModelRegistry: IModelMap = {} as IModelMap;
export function registerModel<TModelName extends keyof IModelMap, TModel extends IModelMap[TModelName]["Model"]>(modelType: TModel) {
    const modelName = modelType.MODEL_NAME as TModelName;
    const ModelContext = createContext<IModelContext<TModel> | undefined>(undefined);

    function Provider({ model, params, children }: IModelProviderProps<TModel>): React.ReactNode {
        return (
            <ModelContext.Provider
                value={{
                    model,
                    params,
                }}
            >
                {children}
            </ModelContext.Provider>
        );
    }

    function useModel<TParams = any>() {
        const context = useContext(ModelContext);
        if (!context) {
            throw new Error(`useModel must be used within a ${modelName} Provider`);
        }
        return context as IModelContext<TModel, TParams>;
    }

    ModelRegistry[modelName] = {
        Model: modelType,
        Provider,
        Context: ModelContext,
        useContext: useModel,
    } as any;
}

export function isModel<TModelName extends keyof IModelMap>(model: any, modelName: TModelName): model is TPickedModel<TModelName> {
    return (
        model &&
        model.constructor &&
        (model.constructor.MODEL_NAME === modelName || (model.constructor.MODEL_NAME === "AuthUser" && modelName === "User"))
    );
}
