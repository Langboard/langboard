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
        setConfiguration(next);
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
                aria-busy={pending || props?.["aria-busy"]}
                workbenchContext={configuration?.hasContext ? <div inert={pending} ref={setSidebarSlot} className="size-full" /> : undefined}
                // Block stale chrome, while the new outlet can still expose Retry/Close.
                workbenchContextHidden={props?.workbenchContextHidden}
            >
                {outlet}
            </DashboardStyledLayoutFrame>
        </WorkbenchLayoutContext.Provider>
    );
}
