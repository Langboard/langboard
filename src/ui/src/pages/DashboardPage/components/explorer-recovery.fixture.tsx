import "@/core/injection";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import ProjectExplorerSidebar from "./ProjectExplorerSidebar";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { AuthUser, Project } from "@/core/models";
import { getUserSettingsStore } from "@/core/stores/UserSettingsStore";
import useAuthStore from "@/core/stores/AuthStore";
import "@/i18n";
import "@/assets/styles/main.css";

const currentUser = AuthUser.Model.fromOne({ uid: "explorer-fixture-user", created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser, state: "loaded", pageLoaded: true });
const client = new QueryClient();
const sections = new URLSearchParams(location.search).has("sections");
const cached = new URLSearchParams(location.search).has("cached");
const data = {
    projects: [{ created_at: new Date(), updated_at: new Date(), uid: "fixture", title: "Retained board", starred: false, project_type: "Other" }],
    columns: [],
};
if (cached)
    client.setQueryData(
        ["get-dashboard-projects", currentUser.uid],
        { projects: Project.Model.fromArray(data.projects, true), columns: [] },
        { updatedAt: 1 }
    );
if (sections) {
    data.projects = Array.from({ length: 36 }, (_, i) => ({
        created_at: new Date(),
        updated_at: new Date(),
        uid: `fixture-${i}`,
        title: `Board ${String(i).padStart(2, "0")}`,
        starred: i < 12,
        project_type: "Other",
        last_activity_at: i >= 12 && i < 18 ? new Date() : null,
    }));
    getUserSettingsStore().updateSettingsByKey("open_cards", {
        [currentUser.uid]: Array.from({ length: 14 }, (_, i) => ({
            projectUID: "fixture-0",
            cardUID: String(i),
            title: `Card ${i}`,
            pinned: false,
            lastFocusedAt: new Date().toISOString(),
        })),
    });
}
let fail = !sections;
api.defaults.adapter = async (config) => {
    const response = { status: 200, statusText: "OK", headers: {}, config };
    if (fail) throw new AxiosError("Unavailable", "ERR_BAD_RESPONSE", config, undefined, { ...response, status: 503, data: {} });
    if (config.url?.includes("cards/available")) return { ...response, data: JSON.parse(config.data) };
    return { ...response, data };
};
function Fixture() {
    return (
        <div className="h-screen max-w-sm">
            <button
                onClick={() => {
                    fail = false;
                }}
            >
                Recover service
            </button>
            {sections && (
                <button
                    onClick={() => {
                        const user =
                            useAuthStore.getState().currentUser?.uid === currentUser.uid
                                ? AuthUser.Model.fromOne({ uid: "other-explorer-user", created_at: new Date(), updated_at: new Date() })
                                : currentUser;
                        useAuthStore.setState({ currentUser: user });
                    }}
                >
                    Switch account
                </button>
            )}
            <ProjectExplorerSidebar />
        </div>
    );
}
const router = createMemoryRouter(
    [
        {
            path: "*",
            element: (
                <AuthProvider>
                    <Fixture />
                </AuthProvider>
            ),
        },
    ],
    { initialEntries: ["/board/fixture"] }
);
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={client}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
