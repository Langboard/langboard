import { useCallback, createContext, forwardRef, useContext, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import Header from "@/components/Header";
import { IHeaderNavItem } from "@/components/Header/types";
import ResizableSidebar, { IResizableSidebarProps } from "@/components/ResizableSidebar";
import Sidebar from "@/components/Sidebar";
import { ISidebarNavItem } from "@/components/Sidebar/types";
import { cn } from "@/core/utils/ComponentUtils";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import ScrollArea from "@/components/base/ScrollArea";
import useScrollToTop from "@/core/hooks/useScrollToTop";
import useAuthStore from "@/core/stores/AuthStore";
import { getUserSettingsStore, useUserSettings } from "@/core/stores/UserSettingsStore";
import ActivityRail, { IActivityRailItem } from "@/components/Layout/ActivityRail";

import WorkbenchStatusBar, { IWorkbenchStatusContext } from "@/components/Layout/WorkbenchStatusBar";

interface IBaseDashboardStyledLayoutProps {
    workbenchStatus?: IWorkbenchStatusContext;
    children: React.ReactNode;
    headerNavs?: IHeaderNavItem[];
    headerTitle?: React.ReactNode;
    sidebarNavs?: ISidebarNavItem[];
    resizableSidebar?: Omit<IResizableSidebarProps, "main">;
    className?: string;
    inert?: bool;
    "aria-busy"?: React.AriaAttributes["aria-busy"];
    scrollAreaMutable?: React.ComponentPropsWithoutRef<typeof ScrollArea.Root>["mutable"];
    activityRailItems?: IActivityRailItem[];
    workbench?: boolean;
    workbenchContext?: React.ReactNode;
    workbenchContextHidden?: boolean;
    mobileWorkbenchContext?: { title: string; icon: string; onClose: () => void; immersive?: boolean };
}

interface IHeaderDashboardStyledLayoutProps extends IBaseDashboardStyledLayoutProps {
    headerNavs: IHeaderNavItem[];
    headerTitle?: React.ReactNode;
}

interface INoHeaderDashboardStyledLayoutProps extends IBaseDashboardStyledLayoutProps {
    headerNavs?: undefined;
    headerTitle?: undefined;
}

interface ISidebarDashboardStyledLayoutProps extends IBaseDashboardStyledLayoutProps {
    sidebarNavs: ISidebarNavItem[];
    resizableSidebar?: undefined;
}

interface IResizableSidebarDashboardStyledLayoutProps extends IBaseDashboardStyledLayoutProps {
    sidebarNavs?: undefined;
    resizableSidebar: Omit<IResizableSidebarProps, "main">;
}

export type TDashboardStyledLayoutProps =
    | IHeaderDashboardStyledLayoutProps
    | INoHeaderDashboardStyledLayoutProps
    | ISidebarDashboardStyledLayoutProps
    | IResizableSidebarDashboardStyledLayoutProps
    | IBaseDashboardStyledLayoutProps;

export const DashboardStyledLayoutFrame = forwardRef<HTMLDivElement, TDashboardStyledLayoutProps>(
    (
        {
            children,
            headerNavs,
            headerTitle,
            sidebarNavs,
            resizableSidebar,
            activityRailItems,
            workbench,
            workbenchContext,
            workbenchContextHidden,
            workbenchStatus,
            mobileWorkbenchContext,
            className,
            scrollAreaMutable,
            ...props
        },
        ref
    ) => {
        const [t] = useTranslation();
        const userUID = useAuthStore((state) => state.currentUser?.uid);
        const sidebarPreferences = useUserSettings("workbench_sidebar_collapsed");
        const savedCollapsed = userUID ? sidebarPreferences?.[userUID] : undefined;
        const [responsiveCollapsed, setResponsiveCollapsed] = useState(false);
        const contextCollapsed = savedCollapsed ?? responsiveCollapsed;
        const setContextCollapsed = useCallback<NonNullable<IResizableSidebarProps["onCollapsedChange"]>>(
            (collapsed, reason) => {
                if (reason === "restore") return;
                if (reason === "responsive" || !userUID) {
                    setResponsiveCollapsed(collapsed);
                    return;
                }
                const store = getUserSettingsStore();
                store.updateSettingsByKey("workbench_sidebar_collapsed", {
                    ...store.settings.workbench_sidebar_collapsed,
                    [userUID]: collapsed,
                });
            },
            [userUID]
        );
        const mobileNavigationTrigger = useRef<HTMLButtonElement>(null);
        const mobileContext = useRef<HTMLElement>(null);
        useLayoutEffect(() => {
            if (mobileWorkbenchContext) mobileContext.current?.focus({ preventScroll: true });
        }, [!!mobileWorkbenchContext]);
        const closeMobileContext = () => {
            mobileWorkbenchContext?.onClose();
            // Restore only explicit dismissal. Route changes keep their destination focus.
            mobileNavigationTrigger.current?.focus({ preventScroll: true });
        };
        useLayoutEffect(() => {
            const panel = mobileContext.current;
            if (!panel || !mobileWorkbenchContext) return;
            // Page-owned portal events bypass the shell's React ancestors.
            // Native bubbling still reaches the physical panel after child handlers.
            const handleEscape = (event: KeyboardEvent) => {
                if (event.key !== "Escape" || event.defaultPrevented) return;
                if (event.target instanceof HTMLElement && event.target.closest("[role=menu], [role=listbox], [role=dialog]")) return;
                event.preventDefault();
                event.stopPropagation();
                closeMobileContext();
            };
            panel.addEventListener("keydown", handleEscape);
            return () => panel.removeEventListener("keydown", handleEscape);
        }, [mobileWorkbenchContext]);
        useLayoutEffect(() => {
            const panel = mobileContext.current;
            const viewport = window.visualViewport;
            if (!panel || !mobileWorkbenchContext?.immersive || !viewport) return;
            const updateViewport = () => {
                panel.style.top = `${viewport.offsetTop}px`;
                panel.style.height = `${Math.max(0, viewport.height)}px`;
            };
            updateViewport();
            viewport.addEventListener("resize", updateViewport);
            viewport.addEventListener("scroll", updateViewport);
            return () => {
                viewport.removeEventListener("resize", updateViewport);
                viewport.removeEventListener("scroll", updateViewport);
                panel.style.removeProperty("top");
                panel.style.removeProperty("height");
            };
        }, [mobileWorkbenchContext?.immersive]);
        const hasWorkbench = workbench || !!activityRailItems;
        const hasActivityRail = activityRailItems?.some((item) => !item.hidden);
        const { scrollableRef, isAtTop, scrollToTop } = useScrollToTop({});

        const main = (
            <ScrollArea.Root viewportId="main" mutable={scrollAreaMutable} className="relative size-full overflow-y-auto" viewportRef={scrollableRef}>
                <main className={cn("relative size-full overflow-y-auto p-4 md:p-6 lg:p-8", className)}>
                    {children}
                    {!isAtTop && (
                        <Button
                            onClick={scrollToTop}
                            size="icon"
                            variant="outline"
                            className={cn(
                                "fixed bottom-[calc(var(--workbench-bottom-inset,0px)+0.5rem)] left-1/2",
                                "inline-flex -translate-x-1/2 transform rounded-full shadow-md"
                            )}
                        >
                            <IconComponent icon="arrow-up" size="4" />
                        </Button>
                    )}
                </main>
            </ScrollArea.Root>
        );

        let sidebar;
        if (sidebarNavs) {
            sidebar = <Sidebar navs={sidebarNavs} main={main} />;
        } else if (resizableSidebar) {
            sidebar = <ResizableSidebar main={main} {...resizableSidebar} compactHeight={hasWorkbench} />;
        } else {
            sidebar = main;
        }

        return (
            <Flex
                direction="col"
                w="full"
                minH="screen"
                ref={ref}
                {...props}
                className={cn(
                    hasWorkbench &&
                        [
                            "[--workbench-bottom-inset:var(--workbench-status-height)]",
                            "[--workbench-status-height:calc(2rem+var(--workbench-status-safe))]",
                            "[--workbench-status-safe:env(safe-area-inset-bottom)]",
                        ].join(" "),
                    hasActivityRail &&
                        [
                            "[--workbench-bottom-inset:calc(var(--workbench-navigation-inset)+var(--workbench-status-height))]",
                            "[--workbench-navigation-inset:calc(3.5rem+1px+env(safe-area-inset-bottom))]",
                            "[--workbench-status-safe:0px] md:[--workbench-navigation-inset:0px]",
                            "md:[--workbench-status-safe:env(safe-area-inset-bottom)]",
                        ].join(" ")
                )}
            >
                {headerNavs && (
                    <Header
                        navs={activityRailItems ? [] : headerNavs}
                        title={headerTitle}
                        compact={hasWorkbench}
                        navigationReady={!props.inert}
                        mobileNavigationTriggerRef={mobileNavigationTrigger}
                        mobileContextRef={mobileContext}
                    />
                )}
                <Box
                    w="full"
                    className={cn(
                        "overflow-y-auto",
                        hasWorkbench
                            ? hasActivityRail
                                ? [
                                      "h-[calc(100dvh-6.25rem-1px-env(safe-area-inset-bottom)-var(--workbench-status-height))]",
                                      "md:h-[calc(100dvh-2.75rem-var(--workbench-status-height))]",
                                  ].join(" ")
                                : "h-[calc(100dvh-2.75rem-var(--workbench-status-height))]"
                            : "min-h-[calc(100vh_-_theme(spacing.16))]"
                    )}
                >
                    {hasWorkbench ? (
                        <div className="flex size-full">
                            {activityRailItems && (
                                <ActivityRail
                                    items={activityRailItems}
                                    contextExpanded={!!workbenchContext && !workbenchContextHidden && !contextCollapsed}
                                    onMobileNavigate={(trigger) => {
                                        mobileNavigationTrigger.current = trigger;
                                    }}
                                    mobileContextRef={mobileContext}
                                />
                            )}
                            <div className="min-w-0 flex-1">
                                <ResizableSidebar
                                    main={<div className="h-full min-h-0 min-w-0 flex-1">{sidebar}</div>}
                                    initialWidth={280}
                                    collapsableWidth={220}
                                    minWidth={220}
                                    maxWidth={420}
                                    compactHeight
                                    floatingHidden
                                    showCollapseButton
                                    collapsed={contextCollapsed}
                                    autoCollapseAt={savedCollapsed === undefined ? 1280 : undefined}
                                    onCollapsedChange={setContextCollapsed}
                                    hidden={!workbenchContext || workbenchContextHidden}
                                >
                                    {workbenchContextHidden ? null : workbenchContext}
                                </ResizableSidebar>
                            </div>
                        </div>
                    ) : (
                        sidebar
                    )}
                </Box>
                {hasWorkbench && <WorkbenchStatusBar context={props.inert ? undefined : workbenchStatus} />}
                {mobileWorkbenchContext && workbenchContext && (
                    <aside
                        aria-label={mobileWorkbenchContext.title}
                        ref={mobileContext}
                        onClick={(event) => {
                            if (event.target instanceof Element && event.target.closest("[data-workbench-context-close]")) {
                                mobileNavigationTrigger.current?.focus({ preventScroll: true });
                            }
                        }}
                        tabIndex={-1}
                        data-workbench-context=""
                        className={cn(
                            "fixed z-[120]",
                            mobileWorkbenchContext.immersive
                                ? "inset-x-0 top-0 h-dvh"
                                : cn(
                                      "bottom-[calc(var(--workbench-bottom-inset)+0.5rem)] left-2 right-2 h-[60dvh]",
                                      "max-h-[calc(100dvh-7rem-env(safe-area-inset-bottom)-var(--workbench-status-height))]"
                                  ),
                            "overflow-hidden bg-background md:hidden",
                            !mobileWorkbenchContext.immersive && "rounded-2xl border shadow-lg"
                        )}
                    >
                        <Flex direction="col" h="full">
                            {!mobileWorkbenchContext.immersive && (
                                <Flex items="center" gap="2" className="shrink-0 border-b px-4 py-3" weight="semibold">
                                    <IconComponent icon={mobileWorkbenchContext.icon} size="4" />
                                    <span>{mobileWorkbenchContext.title}</span>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon-sm"
                                        className="ml-auto"
                                        aria-label={t("common.Close")}
                                        onClick={closeMobileContext}
                                    >
                                        <IconComponent icon="x" size="4" />
                                    </Button>
                                </Flex>
                            )}
                            <Box className="min-h-0 flex-1">{workbenchContext}</Box>
                        </Flex>
                    </aside>
                )}
            </Flex>
        );
    }
);

export interface IWorkbenchLayoutConfiguration {
    routeKey: string;
    props: Omit<TDashboardStyledLayoutProps, "children" | "resizableSidebar" | "workbenchContext">;
    hasContext: boolean;
}

export const WorkbenchLayoutContext = createContext<{
    configure: (configuration: IWorkbenchLayoutConfiguration) => void;
    sidebarSlot: HTMLDivElement | null;
} | null>(null);

function RegisteredWorkbenchLayout({ children, resizableSidebar, workbenchContext, ...props }: TDashboardStyledLayoutProps) {
    const shell = useContext(WorkbenchLayoutContext)!;
    const { key: routeKey } = useLocation();
    useLayoutEffect(() => {
        shell.configure({ routeKey, props, hasContext: !!workbenchContext });
    }, [shell.configure, routeKey, props, workbenchContext]);

    return (
        <>
            {shell.sidebarSlot && workbenchContext && createPortal(workbenchContext, shell.sidebarSlot)}
            {resizableSidebar ? <ResizableSidebar main={children} {...resizableSidebar} compactHeight /> : children}
        </>
    );
}

const DashboardStyledLayout = forwardRef<HTMLDivElement, TDashboardStyledLayoutProps>((props, ref) => {
    const shell = useContext(WorkbenchLayoutContext);
    return shell ? <RegisteredWorkbenchLayout {...props} /> : <DashboardStyledLayoutFrame {...props} ref={ref} />;
});

export default DashboardStyledLayout;
