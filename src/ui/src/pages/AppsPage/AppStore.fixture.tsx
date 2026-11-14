import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toRoutes } from "@/core/routing/workbenchRoutes";
import { ROUTES } from "@/core/routing/constants";
import apps from "./Route";
import settings from "../SettingsPage/Route";
import AppStore from "./AppStore";
import AppTransferDialog from "@/core/apps/AppTransferDialog";
import useAuthStore from "@/core/stores/AuthStore";
import "@/i18n";
import "@/assets/styles/main.css";
const initial = new URLSearchParams(location.search).get("initial") ?? "/apps?project_uid=TestBoard01";
const userUID = new URLSearchParams(location.search).get("actor") ?? "fixture-user";
const transfer = new URLSearchParams(location.search).get("transfer");
if (transfer) useAuthStore.setState({ currentUser: { uid: userUID } as NonNullable<ReturnType<typeof useAuthStore.getState>["currentUser"]> });
const legacy = settings.routes.find((route) => route.path === ROUTES.SETTINGS.APP_STORE)!;
const router = createMemoryRouter(
    toRoutes([
        {
            workbench: true,
            routes: [
                ...apps.routes.map((route) => ({ ...route, element: <AppStore userUID={userUID} /> })),
                legacy,
                { path: "/board/:projectUID/settings/apps", element: <h1>Board app configuration</h1> },
                { path: ROUTES.SETTINGS.APP_PACKAGES, element: <h1>Install management</h1> },
            ],
        },
    ]),
    { initialEntries: [initial] }
);
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        {transfer ? <AppTransferDialog
            app={{ appKey: "tools", projectUID: "TestBoard01", userUID, enabled: transfer === "unspecified" ? undefined : transfer === "enabled" }}
            source={new URLSearchParams(location.search).has("source") ? { cardUID: "fixture-card" } : undefined}
            onClose={() => {}}
        /> : <RouterProvider router={router} />}
    </QueryClientProvider>
);
