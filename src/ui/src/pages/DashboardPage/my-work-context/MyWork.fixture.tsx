import { Fragment, StrictMode } from "react";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { Routing } from "@langboard/core/constants";
import MyWorkPage from "../MyWorkPage";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient as client } from "@/core/helpers/QueryClient";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import BoardRoutePage from "@/pages/BoardPage/RoutePage";
import BoardProxy from "@/pages/BoardPage";
import { BoardController } from "@/core/providers/BoardController";
import { BoardSplitProvider, BoardSplitPaneContext, useBoardSplitContext } from "@/core/providers/BoardSplitProvider";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { usePageNavigate } from "@/core/hooks/usePageNavigate";
import Button from "@/components/base/Button";

const splitFixture = new URLSearchParams(window.location.search).has("split");
function FixturePane() {
    const location = usePageLocation();
    const navigate = usePageNavigate();
    return (
        <>
            <Button onClick={() => navigate("/board/6KZLKlukzZm/Card0000001?filters=keyword:pane#pane")}>Load old card</Button>
            <output data-pane-location>{location.pathname + location.search + location.hash}</output>
            <BoardController projectUID={location.pathname.split("/")[2]}>
                <BoardProxy />
            </BoardController>
        </>
    );
}
function FixtureWorkspace() {
    const split = useBoardSplitContext()!;
    return (
        <>
            <Button onClick={() => split.open("1gkclR8yA5P")}>Open fixture pane</Button>
            {split.panes.map((pane) => (
                <section key={pane.id} data-fixture-pane={pane.id}>
                    <Button onClick={() => split.close(pane.id)}>Close fixture pane</Button>
                    <Button onClick={() => split.focus(pane.id)}>Focus fixture pane</Button>
                    <BoardSplitPaneContext.Provider value={pane.id}>
                        <PageNavigationScope
                            initialEntry={pane.initialEntry}
                            basePath="/board"
                            syncNative={split.focusedPane === pane.id}
                            onLocationChange={(location) => split.rememberLocation(pane.id, location.pathname + location.search + location.hash)}
                        >
                            <FixturePane />
                        </PageNavigationScope>
                    </BoardSplitPaneContext.Provider>
                </section>
            ))}
        </>
    );
}

const user = AuthUser.Model.fromOne({
    uid: "fixture-user",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    type: "user",
    firstname: "Fixture",
    lastname: "User",
    email: "fixture@example.com",
    username: "fixture",
    api_key_role_actions: ["*"],
    setting_role_actions: ["*"],
    mcp_role_actions: ["*"],
    user_groups: [],
    subemails: [],
    preferred_lang: "en",
    notification_unsubs: {},
    industry: "",
    purpose: "",
} as never);
useAuthStore.setState({ currentUser: user, state: "loaded" });
client.setDefaultOptions({ queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity } });
const primary = "6KZLKlukzZm";
const secondary = "1gkclR8yA5P";
const now = new Date().toISOString();
const fixtureUser = {
    uid: "fixture-user",
    created_at: now,
    updated_at: now,
    type: "user",
    firstname: "Fixture",
    lastname: "User",
    email: "fixture@example.invalid",
    username: "fixture",
    user_groups: [],
    subemails: [],
    preferred_lang: "en",
    notification_unsubs: {},
    api_key_role_actions: ["*"],
    setting_role_actions: ["*"],
    mcp_role_actions: ["*"],
    industry: "",
    purpose: "",
};
const projects = [primary, secondary].map((uid, index) => ({
    uid,
    title: index ? "Other project" : "Current board",
    created_at: now,
    updated_at: now,
    owner_uid: fixtureUser.uid,
    project_type: "default",
    archive_visible_days: 30,
    all_members: [fixtureUser],
    invited_member_uids: [],
    starred: false,
    internal_bots: [],
    internal_bot_settings: {},
    current_auth_role_actions: ["*"],
    labels: [],
    description: "",
    view_count: 0,
}));
const columns = (uid: string) =>
    Array.from({ length: 6 }, (_, order) => ({
        uid: `${uid}-column-${order}`,
        project_uid: uid,
        name: `Column ${order}`,
        order,
        created_at: now,
        updated_at: now,
        color: "#444444",
        workflow_stage: "active",
    }));
const card = (uid: string, project: string) => ({
    uid,
    project_uid: project,
    project_column_uid: `${project}-column-0`,
    title: "Board card for restoration",
    created_at: now,
    updated_at: now,
    order: 0,
    count_comment: 0,
    member_uids: [],
    current_auth_role_actions: ["*"],
    project_members: [],
    labels: [],
    relationships: [],
    has_description: false,
    creator: { uid: fixtureUser.uid, type: "user", name: "Fixture User" },
    description: "",
});
const requests: { url: string; params: unknown; actor: string | undefined; aborted?: boolean }[] = [];
let denied = false;
let delay = false;
let release = () => {};
api.defaults.adapter = async (request) => {
    const path = request.url ?? "";
    const actor = useAuthStore.getState().currentUser?.uid;
    const entry = { url: path, params: request.params, actor, aborted: false };
    requests.push(entry);
    const response = { config: request, status: 200, statusText: "OK", headers: {} };
    if (path === Routing.API.DASHBOARD.MY_WORK) {
        request.signal?.addEventListener?.("abort", () => {
            entry.aborted = true;
        });
        if (delay)
            await new Promise<void>((resolve) => {
                release = resolve;
            });
        if (denied)
            throw new AxiosError("Fixture permission revoked", "ERR_BAD_RESPONSE", request, undefined, {
                ...response,
                status: 403,
                statusText: "Forbidden",
                data: {},
            });
        const query = new URLSearchParams(
            Object.entries(request.params)
                .filter(([, value]) => value !== undefined)
                .map(([key, value]) => [String(key), String(value)])
        );
        const result = await fetch(`http://127.0.0.1:5392/dashboard/work/my?${query}`, {
            signal: request.signal as AbortSignal,
            headers: { "x-fixture-actor": actor === fixtureUser.uid ? "1" : "2" },
        });
        const data = await result.json();
        if (!result.ok)
            throw new AxiosError("Native fixture failed", "ERR_BAD_RESPONSE", request, undefined, { ...response, status: result.status, data });
        return { ...response, data };
    }
    if (request.params?.sections === "checklists" || (path.includes("/card/") && ["put", "post"].includes(request.method ?? ""))) {
        const result = await fetch(`http://127.0.0.1:5392${path}`, {
            method: request.method,
            body: request.data,
            headers: { "Content-Type": "application/json" },
            signal: request.signal as AbortSignal,
        });
        const data = await result.json();
        if (!result.ok) throw new AxiosError("Action rejected", "ERR_BAD_RESPONSE", request, undefined, { ...response, status: result.status, data });
        return { ...response, data };
    }
    const uid = path.split("/")[2];
    if (path.startsWith("/auth/")) return { ...response, data: { access_token: "fixture", user: fixtureUser, bots: [] } };
    if (path === `/board/${uid}`)
        return { ...response, data: { project: projects.find((project) => project.uid === uid), project_bot_scopes: [], project_bot_schedules: [] } };
    if (path === `/board/${uid}/cards`)
        return { ...response, data: { cards: [card(`BoardCard-${uid}`, uid)], columns: columns(uid), checklists: [], global_relationships: [] } };
    if (request.method === "get" && /^\/board\/[^/]+\/card\/[^/]+\/decisions$/.test(path))
        return { ...response, data: { items: [], next_cursor: null, target_revision: "fixture" } };
    if (path.startsWith(`/board/${uid}/card/`)) {
        const cardUID = path.split("/")[4];
        if (request.params?.location_only) return { ...response, data: { canonical_project_uid: uid, card_uid: cardUID } };
        return {
            ...response,
            data: {
                card: card(cardUID, uid),
                checklists: [],
                attachments: [],
                linked_wikis: [],
                global_relationships: [],
                project_columns: columns(uid),
                project_labels: [],
                bot_scopes: [],
                execution_receipts: [],
            },
        };
    }
    if (path.includes("/column/dock")) return { ...response, data: { column_uids: [] } };
    if (path.includes("/cards/available")) return { ...response, data: { card_uids: [] } };
    if (path.includes("project"))
        return { ...response, data: { projects, columns: projects.flatMap((project) => columns(project.uid)), records: [], collections: [] } };
    if (path.includes("apps")) return { ...response, data: { apps: [] } };
    return { ...response, data: {} };
};
Object.assign(window, {
    myWorkFixture: {
        primary,
        secondary,
        requests,
        deny: (value: boolean) => {
            denied = value;
        },
        delay: (value: boolean) => {
            delay = value;
        },
        release: () => release(),
        refresh: () => client.invalidateQueries({ queryKey: ["dashboard-my-work"] }),
        switchActor: () =>
            useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...fixtureUser, uid: "other-actor" } as never), state: "loaded" }),
        newIdentity: () => {
            useAuthStore.getState().removeToken();
            useAuthStore.setState({ currentUser: AuthUser.Model.fromOne(fixtureUser as never), state: "loaded" });
        },
        logout: () => useAuthStore.getState().removeToken(),
        refreshToken: () => useAuthStore.getState().updateToken("rotated-token", api),
        cache: () =>
            client
                .getQueryCache()
                .getAll()
                .filter((query) => query.queryKey[0] === "dashboard-my-work")
                .map((query) => ({ key: query.queryKey, data: query.state.data, error: query.state.error?.message })),
    },
});
function LocationReceipt() {
    const location = useLocation();
    return (
        <output hidden data-native-location>
            {location.pathname + location.search + location.hash}
        </output>
    );
}
const strictFixture = new URLSearchParams(window.location.search).has("strict");
window.history.replaceState(null, "", new URLSearchParams(window.location.search).get("entry") ?? "/board/6KZLKlukzZm?filters=keyword:restoration");
const FixtureMode = strictFixture ? StrictMode : Fragment;
createRoot(document.getElementById("root")!).render(
    <FixtureMode>
        <BrowserRouter>
            <LocationReceipt />
            <QueryClientProvider client={client}>
                <PageHeaderProvider>
                    <AuthProvider>
                        <SocketProvider>
                            <Routes>
                                <Route path="/dashboard" element={<MyWorkPage />} />
                                <Route
                                    path="/board/:projectUID/*"
                                    element={
                                        splitFixture ? (
                                            <BoardSplitProvider>
                                                <FixtureWorkspace />
                                            </BoardSplitProvider>
                                        ) : (
                                            <BoardRoutePage />
                                        )
                                    }
                                />
                            </Routes>
                        </SocketProvider>
                    </AuthProvider>
                </PageHeaderProvider>
            </QueryClientProvider>
        </BrowserRouter>
    </FixtureMode>
);
