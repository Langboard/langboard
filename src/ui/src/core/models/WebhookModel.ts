import useWebhookDeletedHandlers from "@/controllers/socket/settings/webhooks/useWebhookDeletedHandlers";
import useWebhookUpdatedHandlers from "@/controllers/socket/settings/webhooks/useWebhookUpdatedHandlers";
import { BaseModel, IBaseModel } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";
import { registerModel } from "@/core/models/ModelRegistry";

export interface Interface extends IBaseModel {
    owner_apps: { key: string; name: string; is_enabled: boolean }[];
    name: string;
    url: string;
    events: string[] | null;
    is_enabled: bool;
    last_used_at: Date | null;
    total_used_count: number;
}

class WebhookModel extends BaseModel<Interface> {
    public static get MODEL_NAME() {
        return "WebhookModel" as const;
    }

    constructor(model: Record<string, unknown>) {
        super(model);

        this.subscribeSocketEvents([useWebhookUpdatedHandlers, useWebhookDeletedHandlers], {
            webhook: this,
        });
    }

    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isString(model.last_used_at)) {
            model.last_used_at = new Date(model.last_used_at);
        }
        model.owner_apps ??= [];
        return model;
    }

    public get owner_apps() {
        return this.getValue("owner_apps") ?? [];
    }

    public set owner_apps(value) {
        this.update({ owner_apps: value });
    }

    public get name() {
        return this.getValue("name");
    }
    public set name(value) {
        this.update({ name: value });
    }

    public get url() {
        return this.getValue("url");
    }
    public set url(value) {
        this.update({ url: value });
    }

    public get events() {
        return this.getValue("events");
    }
    public set events(value) {
        this.update({ events: value });
    }

    public get is_enabled() {
        return this.getValue("is_enabled");
    }
    public set is_enabled(value) {
        this.update({ is_enabled: value });
    }

    public get last_used_at(): Date | null {
        return this.getValue("last_used_at");
    }
    public set last_used_at(value: string | Date | null) {
        this.update({ last_used_at: value as unknown as Date });
    }

    public get total_used_count() {
        return this.getValue("total_used_count");
    }
    public set total_used_count(value) {
        this.update({ total_used_count: value });
    }
}

registerModel(WebhookModel);

export const Model = WebhookModel;
export type TModel = WebhookModel;
