import { useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { DashboardStyledLayout } from "@/components/Layout";
import type { TDashboardStyledLayoutProps } from "@/components/Layout/DashboardStyledLayout";
import { useBoardSplitContext, useBoardSplitPane, useBoardSplitSidebarSlot } from "@/core/providers/BoardSplitProvider";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";

export default function BoardPaneLayout({ boardTitle, ...props }: TDashboardStyledLayoutProps & { boardTitle: string }) {
    const split = useBoardSplitContext();
    const paneID = useBoardSplitPane();
    const sidebarSlot = useBoardSplitSidebarSlot();
    const [t] = useTranslation();
    const publish = split?.publishLayout;
    useLayoutEffect(() => {
        if (paneID) publish?.(paneID, props);
    });
    useLayoutEffect(
        () => () => {
            if (paneID) publish?.(paneID);
        },
        [paneID, publish]
    );
    if (!split || !paneID) return <DashboardStyledLayout {...props} />;
    return (
        <>
            {/* Keep the focused sidebar in the owning pane's chat, navigation and authorization contexts. */}
            {split.focusedPane === paneID && sidebarSlot && props.workbenchContext && createPortal(props.workbenchContext, sidebarSlot)}
            {props.children}
            {split.panes.length > 1 && (
                <div
                    className={cn(
                        "absolute bottom-0 right-0 z-40 hidden",
                        "max-w-[80%] items-center gap-2 rounded-tl-xl border-l border-t",
                        "bg-background px-2 py-1 transition-colors md:flex",
                        split.focusedPane === paneID ? "border-primary/50 bg-primary/10 text-primary" : "border-border text-muted-foreground"
                    )}
                    data-board-pane-footer=""
                    data-disable-change-mode=""
                >
                    <span className="truncate text-xs">{boardTitle}</span>
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="hidden size-6 md:inline-flex"
                        aria-label={`${t("common.Close")} ${boardTitle}`}
                        onClick={() => split.close(paneID)}
                    >
                        <IconComponent icon="x" size="3" />
                    </Button>
                </div>
            )}
        </>
    );
}
