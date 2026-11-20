import { createContext, useContext } from "react";
import type { TDashboardStyledLayoutProps } from "@/components/Layout/DashboardStyledLayout";

export interface IBoardPane {
    id: string;
    initialEntry: string;
}
export interface IBoardSplit {
    panes: IBoardPane[];
    focusedPane: string;
    open: (projectUID: string) => void;
    focus: (id: string) => void;
    close: (id: string, force?: boolean) => boolean;
    rememberLocation: (id: string, entry: string) => void;
    publishLayout: (id: string, props?: TDashboardStyledLayoutProps) => void;
    subscribeLayout: (listener: () => void) => () => void;
    getLayout: () => TDashboardStyledLayoutProps | undefined;
}
export const BoardSplitContext = createContext<IBoardSplit | undefined>(undefined);
export const useBoardSplitContext = () => useContext(BoardSplitContext);
