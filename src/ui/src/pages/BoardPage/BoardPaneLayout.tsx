import { useLayoutEffect } from "react";
import { useTranslation } from "react-i18next";
import { DashboardStyledLayout } from "@/components/Layout";
import type { TDashboardStyledLayoutProps } from "@/components/Layout/DashboardStyledLayout";
import { useBoardSplitContext, useBoardSplitPane } from "@/core/providers/BoardSplitProvider";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";

export default function BoardPaneLayout({ boardTitle, ...props }: TDashboardStyledLayoutProps & { boardTitle: string }) {
    const split = useBoardSplitContext();
    const paneID = useBoardSplitPane();
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
            {props.children}
            {split.panes.length > 1 && (
                <div
                    className={cn(
                        "absolute bottom-[calc(var(--workbench-bottom-inset,0px)+0.5rem)] right-2 z-40 hidden",
                        "max-w-[80%] items-center gap-2 rounded-md border",
                        "bg-background/95 px-2 py-1 shadow-sm md:flex"
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
