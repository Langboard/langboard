import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

interface IUpdateBotStatusParams {
    type: "project_column" | "card";
    botUID: string;
    targetUID: string;
    status: "running" | "stopped";
}

export interface IBotStatusStore {
    botStatusMap: Record<IUpdateBotStatusParams["type"], Record<string, string[]>>;
    updateBotStatus: (params: IUpdateBotStatusParams) => void;
    replaceMap: (map: IBotStatusStore["botStatusMap"]) => void;
}

const useBotStatusStore = create(
    immer<IBotStatusStore>((set) => {
        return {
            botStatusMap: {
                project_column: {},
                card: {},
            },
            updateBotStatus: ({ type, botUID, targetUID, status }: IUpdateBotStatusParams) => {
                set((state) => {
                    const active = state.botStatusMap[type][targetUID] ?? [];
                    if (status === "running") {
                        if (!active.includes(botUID)) state.botStatusMap[type][targetUID] = [...active, botUID];
                    } else if (active.includes(botUID)) {
                        state.botStatusMap[type][targetUID] = active.filter((uid) => uid !== botUID);
                    }
                });
            },
            replaceMap: (map: IBotStatusStore["botStatusMap"]) => {
                set({ botStatusMap: map });
            },
        };
    })
);
export const getBotStatusStore = () => useBotStatusStore.getState();

export const useHasRunningBot = ({ type, targetUID }: Omit<IUpdateBotStatusParams, "botUID" | "status">): bool =>
    useBotStatusStore((state) => (state.botStatusMap[type][targetUID]?.length ?? 0) > 0);

export default useBotStatusStore;
