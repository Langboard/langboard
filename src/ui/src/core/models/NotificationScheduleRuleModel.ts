/* eslint-disable @/max-len */
import useNotificationScheduleRuleDeletedHandlers from "@/controllers/socket/settings/notificationSchedule/useNotificationScheduleRuleDeletedHandlers";
import useNotificationScheduleRuleUpdatedHandlers from "@/controllers/socket/settings/notificationSchedule/useNotificationScheduleRuleUpdatedHandlers";
import { BaseModel, IBaseModel } from "@/core/models/Base";
import { subscribeModelSocketTopic } from "@/core/models/base/socketSubscriptions";
import { registerModel } from "@/core/models/ModelRegistry";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";

export interface Interface extends IBaseModel {
    name: string;
    is_enabled: bool;
    interval_str: string;
    target: string;
    field: string;
    operator: string;
    value?: string | number | bool | null;
    recipients: string[];
    repeat_after_hours: number;
    last_run_at: Date | null;
}

export interface IRuleSchema {
    targets: Array<{
        key: string;
        fields: Array<{
            key: string;
            operators: string[];
        }>;
        recipients: string[];
    }>;
    operators: Record<
        string,
        {
            value_type: "number" | "none" | "dynamic";
            min?: number;
        }
    >;
    values: Record<string, Array<string | bool>>;
}

class NotificationScheduleRuleModel extends BaseModel<Interface> {
    static #isTopicSubscribed = false;
    static #subscribeTimeout: NodeJS.Timeout | undefined = undefined;

    public static get MODEL_NAME() {
        return "NotificationScheduleRuleModel" as const;
    }

    constructor(model: Record<string, unknown>) {
        super(model);

        this.#subscribeTopic();
        this.subscribeSocketEvents([useNotificationScheduleRuleUpdatedHandlers, useNotificationScheduleRuleDeletedHandlers], {
            rule: this,
        });
    }

    #subscribeTopic() {
        if (NotificationScheduleRuleModel.#isTopicSubscribed) {
            return;
        }

        if (NotificationScheduleRuleModel.#subscribeTimeout) {
            clearTimeout(NotificationScheduleRuleModel.#subscribeTimeout);
        }

        NotificationScheduleRuleModel.#subscribeTimeout = setTimeout(() => {
            subscribeModelSocketTopic(ESocketTopic.AppSettings, [ESettingSocketTopicID.NotificationSchedule]);
            NotificationScheduleRuleModel.#isTopicSubscribed = true;
            NotificationScheduleRuleModel.#subscribeTimeout = undefined;
        }, 100);
    }

    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isString(model.last_run_at)) {
            model.last_run_at = new Date(model.last_run_at);
        }
        return model;
    }

    public get name() {
        return this.getValue("name");
    }
    public set name(value) {
        this.update({ name: value });
    }

    public get is_enabled() {
        return this.getValue("is_enabled");
    }
    public set is_enabled(value) {
        this.update({ is_enabled: value });
    }

    public get interval_str() {
        return this.getValue("interval_str");
    }
    public set interval_str(value) {
        this.update({ interval_str: value });
    }

    public get target() {
        return this.getValue("target");
    }
    public set target(value) {
        this.update({ target: value });
    }

    public get field() {
        return this.getValue("field");
    }
    public set field(value) {
        this.update({ field: value });
    }

    public get operator() {
        return this.getValue("operator");
    }
    public set operator(value) {
        this.update({ operator: value });
    }

    public get value() {
        return this.getValue("value");
    }
    public set value(value) {
        this.update({ value });
    }

    public get recipients() {
        return this.getValue("recipients");
    }
    public set recipients(value) {
        this.update({ recipients: value });
    }

    public get repeat_after_hours() {
        return this.getValue("repeat_after_hours");
    }
    public set repeat_after_hours(value) {
        this.update({ repeat_after_hours: value });
    }

    public get last_run_at(): Date | null {
        return this.getValue("last_run_at");
    }
    public set last_run_at(value: string | Date | null) {
        this.update({ last_run_at: value ? new Date(value) : null });
    }
}

registerModel(NotificationScheduleRuleModel);

export const Model = NotificationScheduleRuleModel;
export type TModel = NotificationScheduleRuleModel;
