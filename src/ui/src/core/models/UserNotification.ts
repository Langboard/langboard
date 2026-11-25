/* eslint-disable @typescript-eslint/no-explicit-any */
import * as BotModel from "@/core/models/BotModel";
import * as User from "@/core/models/User";
import { BaseModel, IBaseModel } from "@/core/models/Base";
import { registerModel } from "@/core/models/ModelRegistry";
import { Utils } from "@langboard/core/utils";
import { ENotificationType } from "@/core/models/types/notification.type";

export interface Interface extends IBaseModel {
    uid: string;
    type: ENotificationType;
    notifier_user?: User.Interface;
    notifier_bot?: BotModel.Interface;
    message_vars: Record<string, any>;
    read_at?: Date;
    can_snooze?: boolean;
    records: Record<string, any>;
}

class UserNotification extends BaseModel<Interface> {
    public static override get FOREIGN_MODELS() {
        return {
            notifier_user: User.Model.MODEL_NAME,
            notifier_bot: BotModel.Model.MODEL_NAME,
        };
    }
    override get FOREIGN_MODELS() {
        return UserNotification.FOREIGN_MODELS;
    }
    public static get MODEL_NAME() {
        return "UserNotification" as const;
    }

    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isString(model.read_at)) {
            model.read_at = new Date(model.read_at);
        }
        if (Utils.Type.isString(model.type)) {
            model.type = Utils.String.convertSafeEnum(ENotificationType, model.type);
        }
        if (model.notifier_bot) {
            const botAsUser = { ...model.notifier_bot } as unknown as User.Interface;
            if (botAsUser.type === "bot") {
                model.notifier_bot = {
                    uid: botAsUser.uid,
                    name: botAsUser.firstname,
                    bot_uname: botAsUser.username,
                    avatar: botAsUser.avatar,
                } as unknown as BotModel.Interface;
            }
        }
        return model;
    }

    public get type() {
        return this.getValue("type");
    }
    public set type(value) {
        this.update({ type: value });
    }

    public get notifier_user(): User.TModel | undefined {
        return this.getForeignValue("notifier_user")[0];
    }
    public set notifier_user(value: User.Interface | User.TModel | undefined) {
        this.update({ notifier_user: value });
    }

    public get notifier_bot(): BotModel.TModel | undefined {
        return this.getForeignValue("notifier_bot")[0];
    }
    public set notifier_bot(value: BotModel.Interface | BotModel.TModel | undefined) {
        this.update({ notifier_bot: value });
    }

    public get message_vars() {
        return this.getValue("message_vars");
    }
    public set message_vars(value) {
        this.update({ message_vars: value });
    }

    public get read_at(): Date | undefined {
        return this.getValue("read_at");
    }
    public set read_at(value: string | Date | undefined) {
        this.update({ read_at: value as unknown as Date });
    }

    public get records() {
        return this.getValue("records");
    }
    public set records(value) {
        this.update({ records: value });
    }
}

registerModel(UserNotification);

export const Model = UserNotification;
export type TModel = UserNotification;
