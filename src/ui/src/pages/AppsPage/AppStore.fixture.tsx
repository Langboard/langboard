import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toRoutes } from "@/core/routing/workbenchRoutes";
import { ROUTES } from "@/core/routing/constants";
import apps from "./Route";
import settings from "../SettingsPage/Route";
import AppStore from "./AppStore";
import "@/i18n";
import "@/assets/styles/main.css";
const initial = new URLSearchParams(location.search).get("initial") ?? "/apps?project_uid=2RRwwB5JNm0";
const userUID = new URLSearchParams(location.search).get("actor") ?? "fixture-user";
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
        <RouterProvider router={router} />
    </QueryClientProvider>
);
