import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard, ProjectCheckitem } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import TrackingPage from "./TrackingPage";
import "@/core/injection";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

const params = new URLSearchParams(location.search);
const mode = params.get("mode");
const failures = new Set(mode === "initial-error" ? [1] : mode === "more-error" ? [2] : []);
const requests: { page: number; refer_time: string }[] = [];
const checkitems = Array.from({ length: mode === "empty" ? 0 : mode === "more-error" ? 35 : 3 }, (_, index) => ({
    uid: `tracking-item-${index + 1}`,
    card_uid: "tracking-card",
    checklist_uid: "tracking-list",
    title: mode === "long-titles" ? `VeryLongUnbrokenTrackedTaskTitle${index + 1}` : `Tracked task ${index + 1}`,
    status: ProjectCheckitem.ECheckitemStatus.Stopped,
    order: index,
    accumulated_seconds: 120,
    is_checked: false,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
}));
useAuthStore.setState({
    currentUser: AuthUser.Model.fromOne({ uid: "tracking-reader", created_at: new Date(), updated_at: new Date() }),
    state: "loaded",
});
api.defaults.adapter = async (config) => {
    if (!config.url?.includes("/dashboard/tracking")) throw new Error("Unexpected fixture request");
    const page = Number(config.params.page);
    requests.push({ page, refer_time: String(config.params.refer_time) });
    document.querySelector("[data-requests]")!.textContent = JSON.stringify(requests);
    await new Promise((resolve) => setTimeout(resolve, mode === "loading" ? 1000 : 150));
    if (failures.delete(page))
        throw new AxiosError("PRIVATE RAW ERROR", "ERR_BAD_RESPONSE", config, undefined, {
            config,
            status: 503,
            statusText: "Unavailable",
            headers: {},
            data: {},
        });
    const limit = Number(config.params.limit);
    return {
        config,
        status: 200,
        statusText: "OK",
        headers: {},
        data: {
            projects: [
                { uid: "tracking-project", title: "Tracking project", created_at: "2026-10-26T00:00:00Z", updated_at: "2026-10-26T00:00:00Z" },
            ],
            cards: [
                {
                    uid: "tracking-card",
                    project_uid: "tracking-project",
                    project_column_uid: "tracking-column",
                    title: mode === "long-titles" ? "VeryLongUnbrokenAuthorizedCardTitle" : "Authorized card",
                    description: { content: "", format: "text" },
                    order: 0,
                    created_at: "2026-10-26T00:00:00Z",
                    updated_at: "2026-10-26T00:00:00Z",
                },
            ],
            checkitems: checkitems.slice((page - 1) * limit, page * limit),
        },
    };
};
void i18n.changeLanguage(params.get("lang") ?? "en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <StrictMode>
            <QueryClientProvider client={new QueryClient()}>
                <BrowserRouter>
                    <output data-requests />
                    <button
                        className="btn"
                        onClick={() => {
                            ProjectCard.Model.getModel("tracking-card")!.title = "Updated card";
                        }}
                    >
                        Update card
                    </button>
                    <main id="main" className="h-[550px] overflow-y-auto">
                        <TrackingPage />
                    </main>
                </BrowserRouter>
            </QueryClientProvider>
        </StrictMode>
    )
);
