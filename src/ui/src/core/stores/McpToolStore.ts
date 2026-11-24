import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

export type TMcpTool = {
    name: string;
    description: string;
    input_schema: Record<string, unknown>;
};

interface IMcpToolStore {
    tools: Record<string, TMcpTool>;
    upsertTool: (model: TMcpTool) => void;
    deleteTool: (name: string) => void;
    replaceTools: (models: TMcpTool[]) => void;
}

const useMcpToolStore = create(
    immer<IMcpToolStore>((set) => {
        return {
            tools: {},
            upsertTool: (model: TMcpTool) => {
                set((state) => {
                    state.tools[model.name] = { ...state.tools[model.name], ...model };
                });
            },
            deleteTool: (name: string) => {
                set((state) => {
                    delete state.tools[name];
                });
            },
            replaceTools: (tools: TMcpTool[]) => {
                const newTools: Record<string, TMcpTool> = {};
                tools.forEach((model) => {
                    newTools[model.name] = model;
                });
                set({ tools: newTools });
            },
        };
    })
);

export const getMcpToolStore = () => useMcpToolStore.getState();

export const useMcpTools = () => useMcpToolStore((state) => state.tools);

export const useMcpTool = (name: string): TMcpTool | undefined => useMcpToolStore((state) => state.tools[name]);

export default useMcpToolStore;
