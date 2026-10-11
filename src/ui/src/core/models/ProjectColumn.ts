import useBoardColumnDeletedHandlers from "@/controllers/socket/board/column/useBoardColumnDeletedHandlers";
import useBoardCardCreatedHandlers from "@/controllers/socket/board/useBoardCardCreatedHandlers";
import { BaseModel, IBaseModel } from "@/core/models/Base";
import { registerModel } from "@/core/models/ModelRegistry";

export interface Interface extends IBaseModel {
    project_uid: string;
    name: string;
    description?: string;
    translations?: Record<string, { name?: string; description?: string }>;
    workflow_stage?: string | null;
    order: number;
    is_archive: bool;
}

export interface IStore extends Interface {
    count: number;
}

class ProjectColumn extends BaseModel<IStore> {
    public static get MODEL_NAME() {
        return "ProjectColumn" as const;
    }

    constructor(model: Record<string, unknown>) {
        super(model);

        this.subscribeSocketEvents([useBoardCardCreatedHandlers, useBoardColumnDeletedHandlers], {
            projectUID: this.project_uid,
            columnUID: this.uid,
            column: this,
        });
    }

    public applyMetadata(data: Pick<Interface, "name" | "description" | "translations" | "workflow_stage">) {
        this.update({
            name: data.name,
            ...(data.description !== undefined ? { description: data.description } : {}),
            ...(data.translations !== undefined ? { translations: data.translations } : {}),
            ...(data.workflow_stage !== undefined ? { workflow_stage: data.workflow_stage } : {}),
        });
    }

    public get project_uid() {
        return this.getValue("project_uid");
    }
    public set project_uid(value) {
        this.update({ project_uid: value });
    }

    public get name() {
        return this.getValue("name");
    }
    public set name(value) {
        this.update({ name: value });
    }

    public get order() {
        return this.getValue("order");
    }
    public set order(value) {
        this.update({ order: value });
    }

    public get count() {
        return this.getValue("count");
    }
    public set count(value) {
        this.update({ count: value });
    }

    public get is_archive() {
        return this.getValue("is_archive");
    }
    public set is_archive(value) {
        this.update({ is_archive: value });
    }
}

registerModel(ProjectColumn);

export type TModel = ProjectColumn;
export const Model = ProjectColumn;
