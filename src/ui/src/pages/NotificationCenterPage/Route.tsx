import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { DashboardStyledLayout } from "@/components/Layout";
import { AuthGuard } from "@/core/routing/AuthGuard";
import { ROUTES } from "@/core/routing/constants";
import useAuthStore from "@/core/stores/AuthStore";

const NotificationCenterPage = lazy(() => import("./index"));

function ActorNotificationCenter() {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const [t] = useTranslation();
    return (
        <DashboardStyledLayout headerNavs={[]} headerTitle={t("notification.inbox.title")}>
            <NotificationCenterPage key={actorUID} />
        </DashboardStyledLayout>
    );
}

export default {
    workbench: true,
    routes: [
        {
            path: ROUTES.NOTIFICATION_CENTER,
            element: (
                <AuthGuard>
                    <ActorNotificationCenter />
                </AuthGuard>
            ),
        },
    ],
};
