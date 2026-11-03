import "@/core/injection";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import ProjectExplorerSidebar from "./ProjectExplorerSidebar";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { AuthUser, Project } from "@/core/models";
import { getUserSettingsStore } from "@/core/stores/UserSettingsStore";
import useAuthStore from "@/core/stores/AuthStore";
import { BoardSplitProvider } from "@/core/providers/BoardSplitProvider";
import "@/i18n";
import "@/assets/styles/main.css";

const currentUser = AuthUser.Model.fromOne({ uid: "explorer-fixture-user", created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser, state: "loaded", pageLoaded: true });
const client = new QueryClient();
const sections = new URLSearchParams(location.search).has("sections");
const cached = new URLSearchParams(location.search).has("cached");
const unread = new URLSearchParams(location.search).has("unread");
const unreadProjects = [0, 1, 2, 99, 100, 105, undefined].map((count, index) => ({
    created_at: new Date(),
    updated_at: new Date(),
    uid: `unread-${index}`,
    title: `Unread board ${index} with a long title to exercise truncation`,
    starred: false,
    project_type: "Other",
    board_unread_card_count: count,
    board_has_unread_change: count === undefined || count > 0,
}));
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
let fail = !sections && !unread;
api.defaults.adapter = async (config) => {
    const response = { status: 200, statusText: "OK", headers: {}, config };
    if (config.url === "/account/project-collections") return { ...response, data: { revision: 0, collections: [] } };
    if (fail) throw new AxiosError("Unavailable", "ERR_BAD_RESPONSE", config, undefined, { ...response, status: 503, data: {} });
    if (config.url?.includes("cards/available")) return { ...response, data: JSON.parse(config.data) };
    if (unread)
        return {
            ...response,
            data: {
                projects: unreadProjects,
                columns: [{ uid: "unread-column", project_uid: "unread-2", name: "Doing", order: 0, incomplete_count: 3, open_count: 4 }],
            },
        };
    return { ...response, data };
};
function Fixture() {
    const location = useLocation();
    return (
        <div className="h-screen max-w-sm">
            <button
                onClick={() => {
                    fail = false;
                }}
            >
                Recover service
            </button>
            {unread && (
                <>
                    <button onClick={() => Project.Model.fromOne({ ...unreadProjects[2], board_unread_card_count: 105 }, true)}>
                        Apply live count
                    </button>
                    <button
                        onClick={() =>
                            Project.Model.fromOne({ ...unreadProjects[2], board_unread_card_count: 0, board_has_unread_change: false }, true)
                        }
                    >
                        Mark board read
                    </button>
                    <output aria-label="Current route">{location.pathname + location.search}</output>
                </>
            )}
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
            {unread ? (
                <BoardSplitProvider>
                    <ProjectExplorerSidebar />
                </BoardSplitProvider>
            ) : (
                <ProjectExplorerSidebar />
            )}
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
