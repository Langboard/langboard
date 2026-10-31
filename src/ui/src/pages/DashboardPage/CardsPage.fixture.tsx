import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import CardsPage from "./CardsPage";
import "@/i18n";
import "@/assets/styles/main.css";
import "@/core/injection/StringExtensions";

const params = new URLSearchParams(location.search);
const mode = params.get("mode");
const failures = new Set(mode === "initial-error" ? [1] : mode === "more-error" ? [2] : []);
const requests: number[] = [];
useAuthStore.setState({
    currentUser: AuthUser.Model.fromOne({ uid: "dashboard-reader", created_at: new Date(), updated_at: new Date() }),
    state: "loaded",
});
const cards = Array.from({ length: mode === "empty" ? 0 : 45 }, (_, index) => ({
    uid: `card-${index + 1}`,
    project_uid: "fixture-project",
    project_column_uid: "fixture-column",
    project_column_name: "Doing",
    title: index === 0 ? "DO NOT SHOW RAW TITLE" : `Card ${String(index + 1).padStart(2, "0")}`,
    source_type: index < 2 ? "project_card" : "card",
    created_at: "2026-10-22T00:00:00Z",
    updated_at: "2026-10-22T00:00:00Z",
    archived_at: index === 2 ? "2026-10-22T00:02:00Z" : null,
    ...(index < 2
        ? {
              linked_resource: {
                  type: "project_card",
                  uid: `source-${index}`,
                  project_uid: "source-project",
                  status: index === 0 ? "forbidden" : "available",
                  title: index === 0 ? "STALE FORBIDDEN TITLE" : "Available linked title",
              },
          }
        : {}),
}));
api.defaults.adapter = async (config) => {
    const page = Number(config.params?.page ?? 1);
    requests.push(page);
    document.querySelector("[data-requests]")!.textContent = JSON.stringify(requests);
    await new Promise((resolve) => setTimeout(resolve, 75));
    if (failures.delete(page)) {
        throw new AxiosError("Synthetic dashboard failure", "ERR_BAD_RESPONSE", config, undefined, {
            config,
            status: 503,
            statusText: "Unavailable",
            headers: {},
            data: {},
        });
    }
    const limit = Number(config.params?.limit ?? 30);
    return {
        config,
        status: 200,
        statusText: "OK",
        headers: {},
        data: {
            projects: [{ uid: "fixture-project", title: "Fixture project", created_at: "2026-10-22T00:00:00Z", updated_at: "2026-10-22T00:00:00Z" }],
            cards: cards.slice((page - 1) * limit, page * limit),
        },
    };
};

function Fixture() {
    const location = useLocation();
    const navigate = useNavigate();
    const [renderCount, setRenderCount] = useState(0);
    return (
        <>
            <output data-requests />
            <output data-location>{location.pathname}</output>
            <output data-route-state>{JSON.stringify(location.state)}</output>
            <output data-render-count>{renderCount}</output>
            <button className="btn" onClick={() => setRenderCount((value) => value + 1)}>
                Rerender parent
            </button>
            <button
                className="btn"
                onClick={() => {
                    const card = ProjectCard.Model.getModel("card-3");
                    if (card) {
                        card.title = "Updated reactive title";
                        card.project_column_name = "Review";
                    }
                }}
            >
                Update visible card
            </button>
            <main id="main" className="h-[80vh] overflow-y-auto">
                <Routes>
                    <Route path="/dashboard/cards" element={<CardsPage />} />
                    <Route
                        path="/board/:projectUID/:cardUID"
                        element={
                            <button className="btn" onClick={() => navigate(-1)}>
                                Back to cards
                            </button>
                        }
                    />
                </Routes>
            </main>
        </>
    );
}

const entry = {
    pathname: "/dashboard/cards",
    state:
        mode === "restore"
            ? {
                  page: 9,
                  sort: { key: "title", direction: "desc" },
                  columnFilters: { title: "hidden" },
                  search: "missing",
                  unrelated: "preserved",
              }
            : null,
};
const app = (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <MemoryRouter initialEntries={[entry]}>
            <PageHeaderProvider>
                <Fixture />
            </PageHeaderProvider>
        </MemoryRouter>
    </QueryClientProvider>
);
createRoot(document.getElementById("root")!).render(params.get("strict") === "1" ? <StrictMode>{app}</StrictMode> : app);
