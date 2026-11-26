import { useCallback, useMemo, useState } from "react";
import { useLocation, useOutlet } from "react-router";
import { DashboardStyledLayoutFrame, IWorkbenchLayoutConfiguration, WorkbenchLayoutContext } from "@/components/Layout/DashboardStyledLayout";

/** The router owns the physical shell; pages supply navigation and portal their context content. */
export default function WorkbenchRouteLayout() {
    const { key: routeKey, pathname } = useLocation();
    const outlet = useOutlet();
    const [configuration, setConfiguration] = useState<IWorkbenchLayoutConfiguration>();
    const configure = useCallback((next: IWorkbenchLayoutConfiguration) => {
        // Split chrome registers before its focused pane publishes navigation. Keep the
        // previous shell inert until that pane is ready instead of remounting its controls.
        if (!next.props.headerNavs && !next.props.activityRailItems && !next.hasContext) return;
        setConfiguration((current) => {
            // A destination board publishes its shell while project data is still loading.
            // Do not let that provisional registration clear the pending boundary. The
            // previous physical shell remains mounted and inert until the destination is
            // ready to publish interactive navigation.
            if (
                current &&
                current.routeKey !== next.routeKey &&
                (next.props.inert || next.props["aria-busy"] === true)
            ) {
                return current;
            }
            return next;
        });
    }, []);
    const [sidebarSlot, setSidebarSlot] = useState<HTMLDivElement | null>(null);
    const context = useMemo(() => ({ configure, sidebarSlot }), [sidebarSlot]);
    const pending = configuration?.routeKey !== routeKey;
    const props = configuration?.props;

    return (
        <WorkbenchLayoutContext.Provider value={context}>
            <DashboardStyledLayoutFrame
                {...props}
                workbench
                className={pending && pathname.startsWith("/board/") ? "!h-full !p-0" : props?.className}
                workbenchStatus={pending ? undefined : props?.workbenchStatus}
                inert={props?.inert}
                navigationInert={pending}
                aria-busy={props?.["aria-busy"]}
                workbenchContext={
                    configuration?.hasContext ? <div inert={pending} aria-busy={pending} ref={setSidebarSlot} className="size-full" /> : undefined
                }
                // Block stale chrome, while the new outlet can still expose Retry/Close.
                workbenchContextHidden={props?.workbenchContextHidden}
            >
                {outlet}
            </DashboardStyledLayoutFrame>
        </WorkbenchLayoutContext.Provider>
    );
}
