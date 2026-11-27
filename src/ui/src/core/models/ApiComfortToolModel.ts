import useApiComfortToolDeletedHandlers from "@/controllers/socket/settings/apiComfortTools/useApiComfortToolDeletedHandlers";
import useApiComfortToolUpdatedHandlers from "@/controllers/socket/settings/apiComfortTools/useApiComfortToolUpdatedHandlers";
import { BaseModel, IBaseModel } from "@/core/models/Base";
import { subscribeModelSocketTopic } from "@/core/models/base/socketSubscriptions";
import { registerModel } from "@/core/models/ModelRegistry";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";

export interface Interface extends IBaseModel {
    name: string;
    label: string;
    description: string;
    api_names: string[];
    query: Record<string, unknown>;
    form: Record<string, unknown>;
    api_queries: Record<string, Record<string, unknown>>;
    api_forms: Record<string, Record<string, unknown>>;
    is_default: bool;
}

class ApiComfortToolModel extends BaseModel<Interface> {
    static #isTopicSubscribed = false;
    static #subscribeTimeout: NodeJS.Timeout | undefined = undefined;

    public static get MODEL_NAME() {
        return "ApiComfortToolModel" as const;
    }

    constructor(model: Record<string, unknown>) {
        super(model);

        this.#subscribeTopic();
        this.subscribeSocketEvents([useApiComfortToolUpdatedHandlers, useApiComfortToolDeletedHandlers], {
            apiComfortTool: this,
        });
    }

    #subscribeTopic() {
        if (ApiComfortToolModel.#isTopicSubscribed) {
            return;
        }

        if (ApiComfortToolModel.#subscribeTimeout) {
            clearTimeout(ApiComfortToolModel.#subscribeTimeout);
        }

        ApiComfortToolModel.#subscribeTimeout = setTimeout(() => {
            subscribeModelSocketTopic(ESocketTopic.AppSettings, [ESettingSocketTopicID.ApiComfortTool]);
            ApiComfortToolModel.#isTopicSubscribed = true;
            ApiComfortToolModel.#subscribeTimeout = undefined;
        }, 100);
    }

    public get name() {
        return this.getValue("name");
    }
    public set name(value) {
        this.update({ name: value });
    }

    public get label() {
        return this.getValue("label");
    }
    public set label(value) {
        this.update({ label: value });
    }

    public get description() {
        return this.getValue("description");
    }
    public set description(value) {
        this.update({ description: value });
    }

    public get api_names() {
        return this.getValue("api_names");
    }
    public set api_names(value) {
        this.update({ api_names: value });
    }

    public get query() {
        return this.getValue("query");
    }
    public set query(value) {
        this.update({ query: value });
    }

    public get form() {
        return this.getValue("form");
    }
    public set form(value) {
        this.update({ form: value });
    }

    public get api_queries() {
        return this.getValue("api_queries");
    }
    public set api_queries(value) {
        this.update({ api_queries: value });
    }

    public get api_forms() {
        return this.getValue("api_forms");
    }
    public set api_forms(value) {
        this.update({ api_forms: value });
    }

    public get is_default() {
        return this.getValue("is_default");
    }
    public set is_default(value) {
        this.update({ is_default: value });
    }
}

registerModel(ApiComfortToolModel);

export const Model = ApiComfortToolModel;
export type TModel = ApiComfortToolModel;
