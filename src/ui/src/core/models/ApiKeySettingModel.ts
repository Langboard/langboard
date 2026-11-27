import { BaseModel, IBaseModel } from "@/core/models/Base";
import { registerModel } from "@/core/models/ModelRegistry";
import { Utils } from "@langboard/core/utils";

export const ALLOWED_ALL_IPS = "*";

export enum EApiKeyProvider {
    Hashicorp = "hashicorp",
    Aws = "aws",
    Azure = "azure",
}

export interface Interface extends IBaseModel {
    user_uid: string;
    name: string;
    activated_at?: Date;
    ip_whitelist: string[];
    expires_in_days?: number;
    expires_at?: Date;
}

class ApiKeySettingModel extends BaseModel<Interface> {
    public static get MODEL_NAME() {
        return "ApiKeySettingModel" as const;
    }

    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isString(model.activated_at)) {
            model.activated_at = new Date(model.activated_at);
        }
        if (Utils.Type.isString(model.expires_at)) {
            model.expires_at = new Date(model.expires_at);
        }
        return model;
    }

    public get user_uid() {
        return this.getValue("user_uid");
    }
    public set user_uid(value) {
        this.update({ user_uid: value });
    }

    public get name() {
        return this.getValue("name");
    }
    public set name(value) {
        this.update({ name: value });
    }

    public get activated_at(): Date | undefined {
        return this.getValue("activated_at");
    }
    public set activated_at(value: string | Date | undefined) {
        this.update({ activated_at: value as unknown as Date });
    }

    public get ip_whitelist() {
        return this.getValue("ip_whitelist");
    }
    public set ip_whitelist(value) {
        this.update({ ip_whitelist: value });
    }

    public get expires_in_days() {
        return this.getValue("expires_in_days");
    }
    public set expires_in_days(value) {
        this.update({ expires_in_days: value });
    }

    public get expires_at(): Date | undefined {
        return this.getValue("expires_at");
    }
    public set expires_at(value: string | Date | undefined) {
        this.update({ expires_at: value as unknown as Date });
    }

    public get is_active() {
        return !!this.activated_at;
    }

    public get is_expired() {
        if (!this.expires_at) {
            return false;
        }
        return this.expires_at < new Date();
    }
}

registerModel(ApiKeySettingModel);

export const Model = ApiKeySettingModel;
export type TModel = ApiKeySettingModel;
