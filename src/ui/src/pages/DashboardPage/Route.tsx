import { Navigate, Outlet, RouteObject } from "react-router";
import { AuthGuard } from "@/core/routing/AuthGuard";
import { ROUTES } from "@/core/routing/constants";
import { lazy } from "react";

const ModalPage = lazy(() => import("./ModalPage"));
const loadDashboard = () => import("./index");
const DashboardProxy = lazy(loadDashboard);

// Load code during identity hydration; AuthGuard still owns rendering and data requests.
const initialPath = window.location.pathname;
if (initialPath === ROUTES.DASHBOARD.ROUTE || initialPath.startsWith(`${ROUTES.DASHBOARD.ROUTE}/`)) {
    void loadDashboard().catch(() => {});
    if (initialPath === ROUTES.DASHBOARD.ROUTE || initialPath.startsWith(`${ROUTES.DASHBOARD.PROJECTS.ROUTE}/`)) {
        void import("./ProjectPage").catch(() => {});
    }
}

const routes: RouteObject[] = [
    {
        path: ROUTES.DASHBOARD.ROUTE,
        element: (
            <AuthGuard>
                <DashboardProxy />
                <Outlet />
            </AuthGuard>
        ),
        children: [
            {
                index: true,
                element: <Navigate to={ROUTES.DASHBOARD.PROJECTS.ALL} />,
            },
            {
                path: ROUTES.DASHBOARD.CARDS,
                element: <></>,
            },
            {
                path: ROUTES.DASHBOARD.MY_WORK,
                element: <></>,
            },
            {
                path: ROUTES.DASHBOARD.TRACKING,
                element: <></>,
            },
            {
                path: ROUTES.DASHBOARD.PROJECTS.TAB(":tabType"),
                element: <></>,
            },
            { path: ROUTES.DASHBOARD.INBOX, element: <></> },
            ...createModalRoutes("inbox"),
            ...createModalRoutes("cards"),
            ...createModalRoutes("my-work"),
            ...createModalRoutes("tracking"),
            ...createModalRoutes("projects", ":tabType"),
        ],
    },
];

function createModalRoutes(type: string, tabName?: string): RouteObject[] {
    return [
        {
            path: createModalRoutePath(type, "new-project", tabName),
            element: <ModalPage />,
        },
        {
            path: createModalRoutePath(type, "my-activity", tabName),
            element: <ModalPage />,
        },
    ];
}

function createModalRoutePath(type: string, modal: string, tabName?: string): string {
    if (tabName) {
        return ROUTES.DASHBOARD.PROJECTS.TAB(`${tabName}/${modal}`);
    } else {
        return ROUTES.DASHBOARD.PAGE_TYPE(`${type}/${modal}`);
    }
}

export default {
    workbench: true,
    routes,
};
