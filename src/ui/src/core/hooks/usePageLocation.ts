import { useLocation } from "react-router";
import { usePageNavigationScope } from "@/core/providers/PageNavigationScope";

export function usePageLocation() {
    const location = useLocation();
    return usePageNavigationScope()?.location ?? location;
}
