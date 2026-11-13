import useBoardAppCatalog from "@/controllers/api/board/useBoardAppCatalog";
import type { IActivityRailItem } from "@/components/Layout/ActivityRail";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import useSyncedPinnedApps from "./useSyncedPinnedApps";

export default function usePinnedAppRail({
    userUID,
    projectUID,
    activeKey,
    onLaunch,
}: {
    userUID?: string;
    projectUID?: string;
    activeKey?: string;
    onLaunch?: (key: string) => boolean;
}) {
    const catalog = useBoardAppCatalog(projectUID ?? "", userUID, !!projectUID);
    const preferences = useSyncedPinnedApps(userUID);
    const navigate = usePageNavigateRef();
    const items: IActivityRailItem[] = preferences.pins.map((pin) => ({
        id: `app:${pin.key}`,
        transfer: projectUID && userUID ? { appKey: pin.key, projectUID, userUID,
            enabled: catalog.data?.some((app) => app.key === pin.key && app.is_available !== false && app.binding?.state === "enabled" &&
                ["transfers.receive", "cards.read", "resources.read", "events.receive"].every((capability) =>
                    app.capabilities.includes(capability) && app.binding!.granted_capabilities.includes(capability))) ?? false } : undefined,
        icon: "puzzle",
        label: pin.name,
        mobile: "app",
        active: activeKey === pin.key,
        onClick: () => {
            if (onLaunch?.(pin.key)) return;
            const search = projectUID ? `?project_uid=${encodeURIComponent(projectUID)}` : "";
            navigate(`${ROUTES.APPS.DETAIL(pin.key)}${search}`);
        },
    }));
    return { ...preferences, items };
}
