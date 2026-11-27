/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
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
    immer<IMcpToolStore>((set, get) => {
        return {
            tools: {},
            upsertTool: (model: TMcpTool) => {
                const currentTools = get().tools;
                currentTools[model.name] = {
                    ...(currentTools[model.name] || {}),
                    ...model,
                } as any;
                set({ tools: currentTools });
            },
            deleteTool: (name: string) => {
                const currentTools = get().tools;
                delete currentTools[name];
                set({ tools: currentTools });
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

export const useMcpTools = () => {
    const [tools, setTools] = useState(getMcpToolStore().tools);

    useEffect(() => {
        const off = useMcpToolStore.subscribe((state) => {
            setTools(state.tools);
        });

        return off;
    }, [setTools]);

    return tools;
};

export const useMcpTool = (name: string): TMcpTool | undefined => {
    const [tool, setTool] = useState(getMcpToolStore().tools[name]);

    useEffect(() => {
        const off = useMcpToolStore.subscribe((state) => {
            const newTool = state.tools[name];
            if (Object.keys(tool || {}).length !== Object.keys(newTool || {}).length) {
                setTool(newTool);
                return;
            }

            const entries = Object.entries(tool || {});
            for (let i = 0; i < entries.length; ++i) {
                const [k, v] = entries[i];
                if (newTool?.[k] !== v) {
                    setTool(newTool);
                    return;
                }
            }
        });

        return off;
    }, [tool, setTool, name]);

    return tool;
};

export default useMcpToolStore;
