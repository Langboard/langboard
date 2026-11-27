import * as ProjectCard from "@/core/models/ProjectCard";
import * as User from "@/core/models/User";
import useCardCheckitemCardifiedHandlers from "@/controllers/socket/card/checkitem/useCardCheckitemCardifiedHandlers";
import useCardCheckitemCheckedChangedHandlers from "@/controllers/socket/card/checkitem/useCardCheckitemCheckedChangedHandlers";
import useCardCheckitemDeadlineChangedHandlers from "@/controllers/socket/card/checkitem/useCardCheckitemDeadlineChangedHandlers";
import useCardCheckitemTitleChangedHandlers from "@/controllers/socket/card/checkitem/useCardCheckitemTitleChangedHandlers";
import useCardCheckitemStatusChangedHandlers from "@/controllers/socket/card/checkitem/useCardCheckitemStatusChangedHandlers";
import { BaseModel, IBaseModel } from "@/core/models/Base";
import { registerModel } from "@/core/models/ModelRegistry";
import { Utils } from "@langboard/core/utils";

export enum ECheckitemStatus {
    Started = "started",
    Paused = "paused",
    Stopped = "stopped",
}

export interface Interface extends IBaseModel {
    card_uid: string;
    checklist_uid: string;
    cardified_card?: ProjectCard.IStore;
    user?: User.Interface;
    title: string;
    status: ECheckitemStatus;
    order: number;
    accumulated_seconds: number;
    is_checked: bool;
    deadline_at?: Date;
    initial_timer_started_at?: Date; // This will be used in tracking page of the dashboard
    timer_started_at?: Date;
}

class ProjectCheckitem extends BaseModel<Interface> {
    public static override get FOREIGN_MODELS() {
        return {
            cardified_card: ProjectCard.Model.MODEL_NAME,
            user: User.Model.MODEL_NAME,
        };
    }
    override get FOREIGN_MODELS() {
        return ProjectCheckitem.FOREIGN_MODELS;
    }
    public static get MODEL_NAME() {
        return "ProjectCheckitem" as const;
    }

    constructor(model: Record<string, unknown>) {
        super(model);

        this.subscribeSocketEvents(
            [
                useCardCheckitemCardifiedHandlers,
                useCardCheckitemCheckedChangedHandlers,
                useCardCheckitemDeadlineChangedHandlers,
                useCardCheckitemStatusChangedHandlers,
                useCardCheckitemTitleChangedHandlers,
            ],
            {
                cardUID: this.card_uid,
                checkitem: this,
            }
        );
    }

    public static convertModel(model: Interface): Interface {
        if (Utils.Type.isString(model.status)) {
            model.status = Utils.String.convertSafeEnum(ECheckitemStatus, model.status);
        }
        if (Utils.Type.isString(model.initial_timer_started_at)) {
            model.initial_timer_started_at = new Date(model.initial_timer_started_at);
        }
        if (Utils.Type.isString(model.timer_started_at)) {
            model.timer_started_at = new Date(model.timer_started_at);
        }
        if (Utils.Type.isString(model.deadline_at)) {
            model.deadline_at = new Date(model.deadline_at);
        }
        return model;
    }

    public get card_uid() {
        return this.getValue("card_uid");
    }
    public set card_uid(value) {
        this.update({ card_uid: value });
    }

    public get checklist_uid() {
        return this.getValue("checklist_uid");
    }
    public set checklist_uid(value) {
        this.update({ checklist_uid: value });
    }

    public get cardified_card(): ProjectCard.TModel | undefined {
        return this.getForeignValue("cardified_card")?.[0];
    }
    public set cardified_card(value: ProjectCard.TModel | ProjectCard.Interface | undefined) {
        this.update({ cardified_card: value as ProjectCard.TModel });
    }

    public get user(): User.TModel | undefined {
        return this.getForeignValue("user")?.[0];
    }
    public set user(value: User.TModel | User.Interface | undefined) {
        this.update({ user: value });
    }

    public get title() {
        return this.getValue("title");
    }
    public set title(value) {
        this.update({ title: value });
    }

    public get status() {
        return this.getValue("status");
    }
    public set status(value) {
        this.update({ status: value });
    }

    public get order() {
        return this.getValue("order");
    }
    public set order(value) {
        this.update({ order: value });
    }

    public get accumulated_seconds() {
        return this.getValue("accumulated_seconds");
    }
    public set accumulated_seconds(value) {
        this.update({ accumulated_seconds: value });
    }

    public get is_checked() {
        return this.getValue("is_checked");
    }
    public set is_checked(value) {
        this.update({ is_checked: value });
    }

    public get deadline_at(): Date | undefined {
        return this.getValue("deadline_at");
    }
    public set deadline_at(value: string | Date | undefined) {
        if (Utils.Type.isString(value)) {
            value = new Date(value);
        }
        this.update({ deadline_at: value as unknown as Date });
    }

    public get initial_timer_started_at(): Date | undefined {
        return this.getValue("initial_timer_started_at");
    }
    public set initial_timer_started_at(value: string | Date | undefined) {
        this.update({ initial_timer_started_at: value as unknown as Date });
    }

    public get timer_started_at(): Date | undefined {
        return this.getValue("timer_started_at");
    }
    public set timer_started_at(value: string | Date | undefined) {
        this.update({ timer_started_at: value as unknown as Date });
    }
}

registerModel(ProjectCheckitem);

export type TModel = ProjectCheckitem;
export const Model = ProjectCheckitem;
