import { lazy } from "react";
import { AuthGuard } from "@/core/routing/AuthGuard";
import { ROUTES } from "@/core/routing/constants";

const AppsPage = lazy(() => import("./index"));
export default {
    workbench: true,
    routes: [
        {
            path: ROUTES.APPS.ROUTE,
            element: (
                <AuthGuard>
                    <AppsPage />
                </AuthGuard>
            ),
        },
        {
            path: `${ROUTES.APPS.ROUTE}/:appKey`,
            element: (
                <AuthGuard>
                    <AppsPage />
                </AuthGuard>
            ),
        },
    ],
};
