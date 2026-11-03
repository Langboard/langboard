import { useCallback, useMemo, useState } from "react";
import { useLocation, useOutlet } from "react-router";
import { DashboardStyledLayoutFrame, IWorkbenchLayoutConfiguration, WorkbenchLayoutContext } from "@/components/Layout/DashboardStyledLayout";

/** The router owns the physical shell; pages supply navigation and portal their context content. */
export default function WorkbenchRouteLayout() {
    const { key: routeKey } = useLocation();
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
                workbenchStatus={pending ? undefined : props?.workbenchStatus}
                inert={pending || props?.inert}
                aria-busy={pending || props?.["aria-busy"]}
                workbenchContext={configuration?.hasContext ? <div ref={setSidebarSlot} className="size-full" /> : undefined}
                // Keep the portal target mounted during route registration; inert blocks stale actions.
                workbenchContextHidden={props?.workbenchContextHidden}
            >
                {outlet}
            </DashboardStyledLayoutFrame>
        </WorkbenchLayoutContext.Provider>
    );
}
