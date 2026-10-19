import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { queryClient } from "@/core/helpers/QueryClient";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import MyWorkPage from "./MyWorkPage";
import "@/i18n";
import "@/core/injection";
import "@/assets/styles/main.css";

const params = new URLSearchParams(location.search);
const delayed = params.has("delayed");
let release = () => {};
const requests: { actor: string | undefined; project: string | null; aborted: boolean; completed: boolean }[] = [];
const user = (uid: string) => ({
    created_at: new Date(),
    updated_at: new Date(),
    uid,
    firstname: uid,
    lastname: "Fixture",
    email: `${uid}@example.test`,
    user_groups: [],
    subemails: [],
});
// Publish native actor transitions while preserving the real cache. This isolates
// the query boundary from the separate login handler's global cache cleanup.
const actor = (uid: string) => {
    if (uid === "actor-B" && params.has("retain")) {
        const query = queryClient.getQueryCache().find({ queryKey: ["dashboard-my-work"], exact: false });
        if (query) new QueryObserver(queryClient, { ...query.options, queryKey: query.queryKey }).subscribe(() => {});
    }
    useAuthStore.setState({ state: "loaded", currentUser: AuthUser.Model.fromOne(user(uid)) });
};
actor("actor-A");
api.defaults.adapter = async (config) => {
    const response = { config, status: 200, statusText: "OK", headers: {} };
    if (config.url === Routing.API.AUTH.ABOUT_ME) return { ...response, data: { user: user("actor-A"), bots: [] } };
    if (config.url === Routing.API.DASHBOARD.PROJECTS) {
        return { ...response, data: { projects: ["project-one", "project-two"].map((uid) => ({ uid, title: uid })), columns: [] } };
    }
    if (config.url !== Routing.API.DASHBOARD.MY_WORK) throw new Error(`Unexpected transport: ${config.url}`);
    const uid = useAuthStore.getState().currentUser?.uid;
    const request = { actor: uid, project: config.params?.project_uid ?? null, aborted: false, completed: false };
    requests.push(request);
    config.signal?.addEventListener?.("abort", () => {
        request.aborted = true;
    });
    if (delayed && uid === "actor-A")
        await new Promise<void>((resolve) => {
            release = resolve;
        });
    request.completed = true;
    return {
        ...response,
        data: {
            cards: [
                {
                    uid: `card-${uid}`,
                    title: `${uid} private work`,
                    project_uid: "project-one",
                    project_title: "project-one",
                    project_column_name: "Doing",
                    deadline_at: null,
                    updated_at: "2026-10-19T00:00:00Z",
                    reasons: ["assigned"],
                },
            ],
        },
    };
};
Object.assign(window, {
    myWorkFixture: {
        requests,
        cache: () =>
            queryClient
                .getQueryCache()
                .getAll()
                .filter((query) => query.queryKey[0] === "dashboard-my-work")
                .map((query) => ({ key: query.queryKey, data: query.state.data, error: query.state.error?.message })),
    },
});
function Fixture() {
    const [visible, setVisible] = useState(true);
    const [refreshed, setRefreshed] = useState(false);
    const uid = useAuthStore((state) => state.currentUser?.uid);
    return (
        <>
            <p>Actor: {uid ?? "signed out"}</p>
            <button onClick={() => actor("actor-B")}>Switch actor</button>
            <button onClick={() => useAuthStore.getState().removeToken()}>Logout</button>
            <button
                onClick={() =>
                    void useAuthStore
                        .getState()
                        .updateToken("rotated-token", api)
                        .then(() => setRefreshed(true))
                }
            >
                Refresh token
            </button>
            {refreshed && <p>Token refresh complete</p>}
            <button onClick={() => release()}>Release previous</button>
            <button onClick={() => setVisible((value) => !value)}>Toggle work</button>
            {visible && <MyWorkPage compact={params.has("compact")} projectUID="project-one" />}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/?project_uid=project-one"]}>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
