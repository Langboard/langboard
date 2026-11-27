import type { ApiComfortToolModel } from "@/core/models";

export const COMFORT_TOOL_NAME_PATTERN = /^[A-Za-z0-9_-]+$/;
export const API_COMFORT_TOOL_COLLABORATION_SECTION = "api-comfort-tool";

export interface IApiComfortToolDraft {
    api_names: string[];
    description: string;
    label: string;
    name: string;
}

export const createEmptyComfortToolDraft = (): IApiComfortToolDraft => ({
    api_names: [],
    description: "",
    label: "",
    name: "",
});

export const createComfortToolDraft = (comfortToolName: string, comfortTool: ApiComfortToolModel.TModel): IApiComfortToolDraft => ({
    api_names: comfortTool.api_names,
    description: comfortTool.description,
    label: comfortTool.label,
    name: comfortTool.name || comfortToolName,
});
