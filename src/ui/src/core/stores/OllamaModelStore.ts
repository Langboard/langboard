import { IPullOllamaModelResponse } from "@/controllers/socket/settings/ollama/usePullOllamaModelHandlers";
import { Utils } from "@langboard/core/utils";
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

export type TBaseOllamaModel = {
    name: string;
    size: number;
    digest: string;
    modified_at: Date;
    details: {
        parent_model?: string;
        format: string;
        family: string;
        families: string[] | null;
        parameter_size: string;
        quantization_level: string;
    };
    modelfile?: string;
    parameters?: string;
    template?: string;
    model_info?: {
        [key: string]: string | number | string[];
    };
    capabilities?: string[];
    expires_at?: Date;
    size_vram?: number;
    is_running?: bool;
};

interface IBaseOllamaModel {
    isTracking: bool;
    name: string;
    progress?: number;
}

interface IUntrackedOllamaModel extends IBaseOllamaModel {
    isTracking: false;
}

interface ITrackedOllamaModel extends IBaseOllamaModel {
    isTracking: true;
}

export type TOllamaPullingModel = IUntrackedOllamaModel | ITrackedOllamaModel;

interface IOllamaModelStore {
    models: Record<string, TBaseOllamaModel>;
    pullingModels: Record<string, TOllamaPullingModel>;
    upsertModel: (model: TBaseOllamaModel) => void;
    deleteModel: (name: string) => void;
    replaceModels: (models: TBaseOllamaModel[]) => void;
    upsertPullingModel: (model: TOllamaPullingModel) => void;
    deletePullingModel: (name: string) => void;
    replacePullingModels: (models: (string | TOllamaPullingModel)[]) => void;
}

const useOllamaModelStore = create(
    immer<IOllamaModelStore>((set) => {
        return {
            models: {},
            pullingModels: {},
            upsertModel: (model: TBaseOllamaModel) => {
                set((state) => {
                    state.models[model.name] = { ...state.models[model.name], ...model };
                });
            },
            deleteModel: (name: string) => {
                set((state) => {
                    delete state.models[name];
                });
            },
            replaceModels: (models: TBaseOllamaModel[]) => {
                const newModels: Record<string, TBaseOllamaModel> = {};
                models.forEach((model) => {
                    newModels[model.name] = model;
                });
                set({ models: newModels });
            },
            upsertPullingModel: (model: TOllamaPullingModel) => {
                set((state) => {
                    state.pullingModels[model.name] = model;
                });
            },
            deletePullingModel: (name: string) => {
                set((state) => {
                    delete state.pullingModels[name];
                });
            },
            replacePullingModels: (models: (string | TOllamaPullingModel)[]) => {
                const newModels: Record<string, TOllamaPullingModel> = {};
                models.forEach((model) => {
                    if (Utils.Type.isString(model)) {
                        newModels[model] = { name: model, progress: 0, isTracking: true };
                    } else {
                        newModels[model.name] = model;
                    }
                });
                set({ pullingModels: newModels });
            },
        };
    })
);

export const getOllamaModelStore = () => useOllamaModelStore.getState();

export const useOllamaModel = (name: string): TBaseOllamaModel | undefined => useOllamaModelStore((state) => state.models[name]);

export const useOllamaPullingModel = (name: string): TOllamaPullingModel | undefined => useOllamaModelStore((state) => state.pullingModels[name]);

export const useOllamaPullingModelProgress = (name: string): number => useOllamaModelStore((state) => state.pullingModels[name]?.progress ?? 0);

export const updateProgressCallback =
    ({ onSuccess, onError }: { onSuccess: () => void; onError: (message: string) => void }) =>
    (data: IPullOllamaModelResponse) => {
        const name = data.model;

        if (data.percent !== undefined) {
            getOllamaModelStore().upsertPullingModel({ name, progress: data.percent, isTracking: true });
        }

        if (data.status === "error") {
            getOllamaModelStore().deletePullingModel(name);
            onError(data.error || "Unknown error");
        }

        if (data.status === "success") {
            getOllamaModelStore().deletePullingModel(name);
            onSuccess();
        }
    };

export default useOllamaModelStore;
