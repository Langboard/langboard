import type { IActivityRailItem } from "@/components/Layout/ActivityRail";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { usePinnedApps } from "@/core/stores/UserSettingsStore";

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
    const preferences = usePinnedApps(userUID);
    const navigate = usePageNavigateRef();
    const items: IActivityRailItem[] = preferences.pins.map((pin) => ({
        id: `app:${pin.key}`,
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
