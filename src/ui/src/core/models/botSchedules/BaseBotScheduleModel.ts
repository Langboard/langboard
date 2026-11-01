import { BaseModel, IBaseModel } from "@/core/models/Base";
import { Utils } from "@langboard/core/utils";

export enum ERunningType {
    Infinite = "infinite",
    Duration = "duration",
    Reserved = "reserved",
    Onetime = "onetime",
}

export enum EStatus {
    Pending = "pending",
    Started = "started",
    Stopped = "stopped",
}

export interface Interface extends IBaseModel {
    bot_uid: string;
    running_type: ERunningType;
    status: EStatus;
    interval_str: string;
    start_at?: Date;
    end_at?: Date;
    last_admission?: IAdmission;
}

export interface IAdmission {
    uid: string;
    state: "pending" | "dispatching" | "accepted" | "failed" | "uncertain" | "cancelled";
    attempt: number;
    max_attempts: number;
    next_attempt_at: string;
    expires_at: string;
    error_code: string | null;
}

export const RUNNING_TYPES_WITH_START_AT = [ERunningType.Duration, ERunningType.Reserved, ERunningType.Onetime];
export const RUNNING_TYPES_WITH_END_AT = [ERunningType.Duration];

abstract class BaseBotScheduleModel<TModel extends Interface> extends BaseModel<TModel> {
    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isString(model.running_type)) {
            model.running_type = Utils.String.convertSafeEnum(ERunningType, model.running_type);
        }
        if (Utils.Type.isString(model.status)) {
            model.status = Utils.String.convertSafeEnum(EStatus, model.status);
        }
        if (Utils.Type.isString(model.start_at)) {
            model.start_at = new Date(model.start_at);
        }
        if (Utils.Type.isString(model.end_at)) {
            model.end_at = new Date(model.end_at);
        }
        return model;
    }

    public get bot_uid() {
        return this.getValue("bot_uid");
    }

    public set bot_uid(value) {
        this.update({ bot_uid: value });
    }

    public get last_admission() {
        return this.getValue("last_admission");
    }

    public get running_type() {
        return this.getValue("running_type");
    }
    public set running_type(value) {
        this.update({ running_type: value });
    }

    public get status() {
        return this.getValue("status");
    }
    public set status(value) {
        this.update({ status: value });
    }

    public get interval_str() {
        return this.getValue("interval_str");
    }
    public set interval_str(value) {
        this.update({ interval_str: value });
    }

    public get start_at(): Date | undefined {
        return this.getValue("start_at");
    }
    public set start_at(value: string | Date | undefined) {
        this.update({ start_at: value as unknown as Date });
    }

    public get end_at(): Date | undefined {
        return this.getValue("end_at");
    }
    public set end_at(value: string | Date | undefined) {
        this.update({ end_at: value as unknown as Date });
    }
}

export const Model = BaseBotScheduleModel;
export type TModel = BaseBotScheduleModel<Interface>;
