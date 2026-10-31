import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project } from "@/core/models";
import ProjectCompactItem from "./ProjectCompactItem";
import ProjectItem from "./ProjectItem";
import i18n from "@/i18n";
import "@/core/injection/StringExtensions";
import "@/assets/styles/main.css";
import { queryClient } from "@/core/helpers/QueryClient";
import { api } from "@/core/helpers/Api";
import { Routing } from "@langboard/core/constants";
import useAuthStore from "@/core/stores/AuthStore";
import useSocketStore from "@/core/stores/SocketStore";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import useGetCards from "@/controllers/api/board/useGetCards";
import { installNativeSocketFixture } from "@/pages/BoardPage/components/board/wiki-summary-tests/nativeSocket.fixture";

await i18n.changeLanguage(new URLSearchParams(location.search).get("lang") ?? "en-US");
const values = [0, 1, 2, 99, 100, 105, undefined];
const records = values.map((count, index) => ({
    uid: `unread-${index}`,
    title: `Unread board ${index}`,
    owner_uid: "owner",
    project_type: "SW",
    created_at: new Date(),
    updated_at: new Date(),
    starred: true,
    all_members: [],
    current_auth_role_actions: [],
    board_has_unread_change: count === undefined || count > 0,
    board_unread_card_count: count,
}));
const projects = Project.Model.fromArray(records);
function Fixture() {
    return (
        <main className="space-y-4 p-4">
            <button onClick={() => Project.Model.fromOne({ ...records[2], board_unread_card_count: 105 }, true)}>Apply live count</button>
            <button onClick={() => Project.Model.fromOne({ ...records[2], board_unread_card_count: 0, board_has_unread_change: false }, true)}>
                Mark board read
            </button>
            <section aria-label="Compact">
                {projects.map((project) => (
                    <ProjectCompactItem key={project.uid} project={project} updateStarredProjects={() => {}} />
                ))}
            </section>
            <section aria-label="Cards" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {projects.map((project) => (
                    <div data-project-uid={project.uid} key={project.uid}>
                        <ProjectItem project={project} updateStarredProjects={() => {}} />
                    </div>
                ))}
            </section>
        </main>
    );
}
const live = new URLSearchParams(location.search).has("live");
let count = 2;
let delayNext = false;
let release = () => {};
const requests: { count: number; actor: string | undefined; aborted: boolean }[] = [];
function selectActor(uid: string) {
    useAuthStore.setState({ state: "loaded", currentUser: AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() }) });
}
if (live) {
    selectActor("reader");
    installNativeSocketFixture();
    api.defaults.adapter = async (config) => {
        const response = { config, status: 200, statusText: "OK", headers: {} };
        if (config.url === Routing.API.DASHBOARD.PROJECTS) {
            const request = { count, actor: useAuthStore.getState().currentUser?.uid, aborted: false };
            requests.push(request);
            config.signal?.addEventListener?.("abort", () => {
                request.aborted = true;
            });
            if (delayNext) {
                delayNext = false;
                await new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            return {
                ...response,
                data: {
                    projects: [
                        { ...records[2], uid: "live-board", board_unread_card_count: request.count, board_has_unread_change: request.count > 0 },
                    ],
                    columns: [],
                },
            };
        }
        if (config.url?.includes("metadata")) return { ...response, data: { metadata: {} } };
        if (config.url?.includes("/cards")) return { ...response, data: { cards: [], columns: [], checklists: [], global_relationships: [] } };
        throw new Error(`Unexpected fixture transport: ${config.url}`);
    };
    Object.assign(window, {
        dashboardUnreadFixture: {
            requests,
            delay: () => {
                delayNext = true;
                void queryClient.invalidateQueries({ queryKey: ["get-dashboard-projects"] });
            },
            release: () => release(),
            setCount: (value: number) => {
                count = value;
            },
            changeActor: (uid: string) => {
                useAuthStore.getState().removeToken();
                selectActor(uid);
            },
            reconnect: () =>
                Object.values(useSocketStore.getState().getStore().defaultEvents.open ?? {})
                    .flat()
                    .forEach((callback) => callback(new Event("open"))),
        },
    });
}
function OtherConsumer() {
    useGetProjects();
    return null;
}
function LiveFixture() {
    const [read, setRead] = useState(false);
    const result = useGetProjects();
    useGetCards({ project_uid: "live-board" }, { enabled: read });
    return (
        <main className="space-y-4 p-4">
            <button onClick={() => setRead(true)}>Read native board</button>
            {result.data?.projects.map((project) => (
                <section key={project.uid} aria-label="Live board">
                    <ProjectCompactItem project={project} updateStarredProjects={() => {}} />
                    <ProjectItem project={project} updateStarredProjects={() => {}} />
                </section>
            ))}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={queryClient}>
        <MemoryRouter>
            {live ? (
                <>
                    <LiveFixture />
                    <OtherConsumer />
                </>
            ) : (
                <Fixture />
            )}
        </MemoryRouter>
    </QueryClientProvider>
);
