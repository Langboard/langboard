import "@/core/injection";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import ProjectExplorerSidebar from "./ProjectExplorerSidebar";
import { AuthUser, Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import "@/i18n";
import "@/assets/styles/main.css";

const currentUser = AuthUser.Model.fromOne({ uid: "explorer-fixture-user", created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser, state: "loaded" });
const client = new QueryClient();
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
let fail = true;
api.defaults.adapter = async (config) => {
    const response = { status: 200, statusText: "OK", headers: {}, config };
    if (fail) throw new AxiosError("Unavailable", "ERR_BAD_RESPONSE", config, undefined, { ...response, status: 503, data: {} });
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
            <ProjectExplorerSidebar />
        </div>
    );
}
const router = createMemoryRouter([{ path: "*", element: <Fixture /> }], { initialEntries: ["/board/fixture"] });
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={client}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
