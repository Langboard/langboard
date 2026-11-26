import { useEffect } from "react";
import { calls } from "./PageTitleLocale.stats.fixture";
export default function useSubscriptionBoundary() {
    useEffect(() => {
        calls.subscription++;
    }, []);
}
