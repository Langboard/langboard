import { flushSync } from "react-dom";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router";
import { BoardController } from "@/core/providers/BoardController";
import { BoardSplitChrome, BoardSplitPaneContext, BoardSplitProvider, useBoardSplitContext } from "@/core/providers/BoardSplitProvider";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import BoardProxy from "@/pages/BoardPage";
import { BOARD_REFERENCE_DRAG_TYPE } from "@/pages/DashboardPage/components/BoardReferenceDrag";
import { ScreenMap } from "@/core/utils/VariantUtils";
import { cn } from "@/core/utils/ComponentUtils";

function BoardPane() {
    const projectUID = usePageLocation().pathname.split("/")[2];
    return (
        <BoardController projectUID={projectUID}>
            <BoardProxy />
        </BoardController>
    );
}

function BoardWorkspace() {
    const [t] = useTranslation();
    const split = useBoardSplitContext()!;
    const [mobile, setMobile] = useState(window.innerWidth < ScreenMap.size.md);
    useEffect(() => {
        const resize = () => setMobile(window.innerWidth < ScreenMap.size.md);
        window.addEventListener("resize", resize);
        return () => window.removeEventListener("resize", resize);
    }, []);
    return (
        <BoardSplitChrome>
            <div className="flex size-full min-w-0" data-board-split-workspace="">
                {split.panes.map((pane) => (
                    <section
                        key={pane.id}
                        data-board-pane={pane.id}
                        data-board-pane-focused={split.focusedPane === pane.id ? "true" : "false"}
                        aria-label={t("common.Board pane")}
                        hidden={mobile && split.focusedPane !== pane.id}
                        className={cn(
                            "relative min-w-0 flex-1 overflow-hidden",
                            mobile && split.focusedPane !== pane.id ? "hidden" : "block",
                            split.panes.length > 1 && !mobile && "isolate border-2",
                            split.focusedPane === pane.id ? "border-primary" : "border-transparent"
                        )}
                        onPointerDownCapture={() => {
                            if (split.focusedPane !== pane.id) flushSync(() => split.focus(pane.id));
                        }}
                        onFocusCapture={() => split.focus(pane.id)}
                        onDragOver={(event) => {
                            if (mobile || split.panes.length >= 2 || !event.dataTransfer.types.includes(BOARD_REFERENCE_DRAG_TYPE)) return;
                            event.preventDefault();
                            event.stopPropagation();
                            event.dataTransfer.dropEffect = "copy";
                        }}
                        onDrop={(event) => {
                            if (!event.dataTransfer.types.includes(BOARD_REFERENCE_DRAG_TYPE)) return;
                            event.preventDefault();
                            event.stopPropagation();
                            split.open(event.dataTransfer.getData(BOARD_REFERENCE_DRAG_TYPE));
                        }}
                    >
                        <BoardSplitPaneContext.Provider value={pane.id}>
                            <PageNavigationScope
                                initialEntry={pane.initialEntry}
                                basePath="/board"
                                syncNative={split.focusedPane === pane.id}
                                onLocationChange={(location) => split.rememberLocation(pane.id, location.pathname + location.search + location.hash)}
                            >
                                <BoardPane />
                            </PageNavigationScope>
                        </BoardSplitPaneContext.Provider>
                    </section>
                ))}
            </div>
        </BoardSplitChrome>
    );
}

export default function BoardRoutePage(): React.JSX.Element {
    return (
        <BoardSplitProvider>
            <BoardWorkspace />
            <Outlet />
        </BoardSplitProvider>
    );
}
