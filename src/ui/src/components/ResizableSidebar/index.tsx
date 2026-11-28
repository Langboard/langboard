import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Floating from "@/components/base/Floating";
import IconComponent from "@/components/base/IconComponent";
import useResizeEvent from "@/core/hooks/useResizeEvent";
import { cn } from "@/core/utils/ComponentUtils";
import { ScreenMap } from "@/core/utils/VariantUtils";
import { Utils } from "@langboard/core/utils";

export interface IResizableSidebarProps {
    main: React.ReactNode;
    children: React.ReactNode;
    initialWidth: number;
    defaultCollapsed?: bool;
    collapsed?: boolean;
    collapsableWidth?: number;
    floatingIcon?: string;
    floatingTitle?: string;
    floatingFullScreen?: bool;
    floatingHidden?: bool;
    hidden?: bool;
    widthCssVariable?: string;
    compactHeight?: bool;
    minWidth?: number;
    maxWidth?: number;
    showCollapseButton?: bool;
    autoCollapseAt?: number;
    onCollapsedChange?: (collapsed: bool, reason?: "user" | "responsive" | "restore") => void;
}

function ResizableSidebar({
    main,
    children,
    initialWidth,
    collapsableWidth = 100,
    defaultCollapsed = false,
    collapsed,
    floatingIcon = "plus",
    floatingTitle = "common.Actions",
    floatingFullScreen = false,
    floatingHidden,
    hidden,
    widthCssVariable,
    compactHeight,
    minWidth,
    maxWidth,
    showCollapseButton = true,
    autoCollapseAt,
    onCollapsedChange,
}: IResizableSidebarProps) {
    const [t] = useTranslation();
    const [localCollapsed, setIsCollapsed] = useState(defaultCollapsed);
    const isCollapsed = collapsed ?? localCollapsed;
    useEffect(() => {
        onCollapsedChange?.(isCollapsed, "restore");
    }, [isCollapsed, onCollapsedChange]);
    const [isMobile, setIsMobile] = useState(window.innerWidth < ScreenMap.size.md);

    const collapsedWidth = 28;
    const expandedWidthRef = useRef(initialWidth);

    if (collapsableWidth < 100) {
        throw new Error("collapsableWidth must be greater than 100");
    }

    useResizeEvent(
        {
            doneCallback: () => {
                setIsMobile(window.innerWidth < ScreenMap.size.md);
            },
        },
        [setIsMobile]
    );

    useEffect(() => {
        if (!widthCssVariable) {
            return;
        }

        document.documentElement.style.setProperty(widthCssVariable, hidden ? "0px" : `${isCollapsed ? collapsedWidth : expandedWidthRef.current}px`);

        return () => {
            document.documentElement.style.removeProperty(widthCssVariable);
        };
    }, [isCollapsed, hidden, widthCssVariable]);

    const sidebarIdRef = useRef(`resizable-sidebar-${Utils.String.Token.shortUUID()}`);
    const setCollapsedAttr = (collapsed: bool, sidebar?: HTMLElement, widthSize?: number) => {
        sidebar = sidebar ?? document.getElementById(sidebarIdRef.current)!;
        if (!widthSize) {
            if (collapsed) {
                widthSize = collapsedWidth;
            } else {
                widthSize = expandedWidthRef.current;
            }
        }

        if (!collapsed) expandedWidthRef.current = widthSize;
        sidebar.style.maxWidth = `${widthSize}px`;
        if (widthCssVariable) {
            document.documentElement.style.setProperty(widthCssVariable, hidden ? "0px" : `${widthSize}px`);
        }

        sidebar.setAttribute("data-collapsed", collapsed ? "true" : "false");
    };

    useEffect(() => {
        const sidebar = document.getElementById(sidebarIdRef.current);
        const container = sidebar?.parentElement;
        if (!sidebar || !container || !autoCollapseAt || hidden) return;
        let previousWidth = -1;
        const update = () => {
            const width = container.clientWidth;
            if (width === previousWidth) return;
            previousWidth = width;
            if (width > 0 && width <= autoCollapseAt) {
                sidebar.style.maxWidth = `${collapsedWidth}px`;
                sidebar.setAttribute("data-collapsed", "true");
                setIsCollapsed(true);
                onCollapsedChange?.(true, "responsive");
            }
        };
        const observer = new ResizeObserver(update);
        observer.observe(container);
        update();
        return () => observer.disconnect();
    }, [autoCollapseAt, hidden, onCollapsedChange]);

    const startResizing = (originalEvent: React.MouseEvent<HTMLDivElement, MouseEvent>) => {
        document.documentElement.style.cursor = "e-resize";
        document.documentElement.style.userSelect = "none";

        const target = originalEvent.currentTarget;
        const sidebar = target.parentElement!;
        const originalWidth = parseFloat(getComputedStyle(sidebar, null).getPropertyValue("width").replace("px", ""));
        const originalMouseX = originalEvent.pageX;

        target.setAttribute("data-selected", "true");
        sidebar.setAttribute("data-resizing", "true");

        const handleResizing = (event: MouseEvent) => {
            const width = originalWidth + (event.pageX - originalMouseX);
            if (minWidth) {
                const nextCollapsed = width < (collapsableWidth + collapsedWidth) / 2;
                const wasCollapsed = sidebar.getAttribute("data-collapsed") === "true";
                // Animate threshold crossings; ordinary width tracking follows the pointer immediately.
                if (nextCollapsed || wasCollapsed) sidebar.removeAttribute("data-resizing");
                else sidebar.setAttribute("data-resizing", "true");
                setCollapsedAttr(nextCollapsed, sidebar, nextCollapsed ? collapsedWidth : Math.max(minWidth, Math.min(maxWidth ?? width, width)));
                return;
            }
            if (width > collapsableWidth) {
                setCollapsedAttr(false, sidebar, width);
            } else if (width <= collapsableWidth && width >= (collapsableWidth + collapsedWidth) / 2) {
                setCollapsedAttr(false, sidebar);
            } else {
                setCollapsedAttr(true, sidebar);
            }
        };

        const stopResizing = () => {
            document.documentElement.style.cursor = "";
            document.documentElement.style.userSelect = "";
            sidebar.removeAttribute("data-resizing");
            target.removeAttribute("data-selected");
            window.removeEventListener("mousemove", handleResizing);
            window.removeEventListener("mouseup", stopResizing);
            const nextCollapsed = sidebar.getAttribute("data-collapsed") === "true";
            setIsCollapsed(nextCollapsed);
            onCollapsedChange?.(nextCollapsed, "user");
        };

        window.addEventListener("mousemove", handleResizing);
        window.addEventListener("mouseup", stopResizing);
    };

    return (
        <>
            <Box
                display={{ initial: "block", md: "flex" }}
                w="full"
                className={cn(compactHeight ? "h-full min-h-0" : "h-[calc(100vh_-_theme(spacing.16))]", "transition-all duration-200 ease-in-out")}
            >
                <Box
                    position="relative"
                    display={{ initial: "hidden", md: hidden ? "hidden" : "block" }}
                    size="full"
                    className={cn(
                        "group/sidebar border-r transition-all duration-200 data-[collapsed=true]:border-transparent",
                        "data-[resizing=true]:transition-none motion-reduce:transition-none"
                    )}
                    style={{ maxWidth: `${isCollapsed ? collapsedWidth : expandedWidthRef.current}px` }}
                    data-collapsed={isCollapsed ? "true" : "false"}
                    data-workbench-sidebar=""
                    id={sidebarIdRef.current}
                    hidden={hidden}
                >
                    <aside
                        className={cn(
                            "sticky z-50 flex size-full flex-col items-start text-sm font-medium group-data-[collapsed=true]/sidebar:hidden"
                        )}
                    >
                        {!isMobile && children}
                    </aside>

                    <Box
                        position="absolute"
                        top="0"
                        z="50"
                        h="full"
                        cursor="e-resize"
                        className={cn(
                            "resizer -right-px w-[3px] bg-transparent opacity-85 transition-colors",
                            "duration-200 ease-in-out hover:bg-primary data-[selected=true]:bg-primary"
                        )}
                        onMouseDown={startResizing}
                    />

                    {showCollapseButton && (
                        <Button
                            type="button"
                            variant="ghost"
                            aria-label={t(isCollapsed ? "common.Expand" : "common.Collapse")}
                            onClick={() => {
                                setCollapsedAttr(!isCollapsed);
                                setIsCollapsed(!isCollapsed);
                                onCollapsedChange?.(!isCollapsed, "user");
                            }}
                            className={cn(
                                "absolute right-0 top-1/2 z-50 size-7 -translate-y-1/2 border-0 bg-transparent p-0 shadow-none",
                                "text-muted-foreground hover:bg-transparent hover:text-foreground",
                                "group-data-[resizing=true]/sidebar:hidden"
                            )}
                        >
                            <IconComponent icon={isCollapsed ? "chevron-right" : "chevron-left"} size="4" />
                        </Button>
                    )}
                </Box>
                {main}
            </Box>
            {!hidden && !floatingHidden && (
                <Floating.Button.Root fullScreen={floatingFullScreen}>
                    <Floating.Button.Content>
                        {floatingFullScreen && <Floating.Button.CloseButton />}
                        {isMobile && children}
                    </Floating.Button.Content>
                    <Floating.Button.Trigger icon={floatingIcon} title={floatingTitle} titleSide="right" />
                </Floating.Button.Root>
            )}
        </>
    );
}

export default ResizableSidebar;
