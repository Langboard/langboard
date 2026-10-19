import "@/core/injection";
import "@/i18n";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, Project, ProjectColumn, BotModel, ProjectBotScope } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { BoardBotScopeList } from "@/pages/BoardPage/components/board/BoardBotScope";
import BoardColumnMoreMenuBotList from "@/pages/BoardPage/components/board/BoardColumnMoreMenuBotList";
import { MemoryRouter } from "react-router";
import { BoardProvider } from "@/core/providers/BoardProvider";
import MoreMenu from "@/components/MoreMenu";
import useGetProject from "./useGetProject";
import useGetCards from "./useGetCards";
const scenario = new URLSearchParams(location.search).get("scenario");
const base = { created_at: new Date(), updated_at: new Date() };
const project = Project.Model.fromOne({
    ...base,
    uid: "automation-board",
    title: "Automation board",
    all_members: [],
    current_auth_role_actions: ["read", "update"],
});
const column = ProjectColumn.Model.fromOne({ ...base, uid: "automation-column", project_uid: project.uid, name: "Column", order: 0 });
const user = AuthUser.Model.fromOne({ ...base, uid: "reader" });
useAuthStore.setState({ currentUser: user });
BotModel.Model.fromOne({ ...base, uid: "bot", name: "Native bot", bot_uname: "native-bot" });
ProjectBotScope.Model.fromOne({ ...base, uid: "other-scope", project_uid: "other-board", bot_uid: "bot", conditions: [], is_frozen: false });
let release = () => {};
let report: (url: string) => void = () => {};
let automationReads = 0;
api.defaults.adapter = async (config) => {
    report(`${config.url}${config.params ? `?include_automation=${config.params.include_automation}` : ""}`);
    const response = { status: 200, statusText: "OK", headers: {}, config };
    if (config.url?.endsWith("/automation")) {
        automationReads++;
        const read = automationReads;
        if (scenario === "slow" || (scenario === "actor" && read === 1) || scenario === "revoke")
            await new Promise<void>((resolve) => {
                release = resolve;
            });
        if (scenario === "missing" || (scenario === "fail" && automationReads === 1))
            throw new AxiosError("Unavailable", "ERR_BAD_RESPONSE", config, undefined, {
                ...response,
                status: scenario === "missing" ? 404 : 500,
                data: {},
            });
        return {
            ...response,
            data: {
                project_bot_scopes:
                    scenario === "actor" && read > 1
                        ? []
                        : [
                              {
                                  ...base,
                                  uid: "loaded-scope",
                                  project_uid: scenario === "foreign" ? "other-board" : project.uid,
                                  bot_uid: "bot",
                                  conditions: [],
                                  is_frozen: false,
                              },
                          ],
                project_bot_schedules: [],
                column_bot_scopes: [],
                column_bot_schedules: [],
            },
        };
    }
    if (config.url?.includes("graph")) return { ...response, data: { approvals: [] } };
    if (config.url?.endsWith("/dock")) return { ...response, data: { revision: 0, column_uids: [] } };
    if (config.url?.includes("metadata")) return { ...response, data: { metadata: {} } };
    if (config.url?.endsWith("/cards"))
        return {
            ...response,
            data: {
                cards: [],
                columns: [{ ...base, uid: column.uid, project_uid: project.uid, name: "Column", order: 0 }],
                checklists: [],
                global_relationships: [],
            },
        };
    return { ...response, data: { project: { ...base, uid: project.uid, title: project.title, current_auth_role_actions: ["read", "update"] } } };
};
function Initial() {
    useGetProject({ uid: project.uid });
    useGetCards({ project_uid: project.uid });
    return null;
}
function Fixture() {
    const [opened, setOpened] = useState(false);
    const [shared, setShared] = useState(false);
    const [requests, setRequests] = useState<string[]>([]);
    const [hydrated, setHydrated] = useState(false);
    report = (url) => setRequests((previous) => [...previous, url]);
    return (
        <>
            <Initial />
            <button onClick={() => setOpened(true)}>Open panel</button>
            <button onClick={() => setShared(true)}>Open shared panel</button>
            <button
                onClick={() => {
                    useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...base, uid: "next-reader" }) });
                }}
            >
                Switch actor
            </button>
            <button
                onClick={() => {
                    project.current_auth_role_actions = [];
                }}
            >
                Revoke
            </button>
            <button onClick={() => release()}>Release</button>
            <button onClick={() => setHydrated(!!ProjectBotScope.Model.getModel("loaded-scope"))}>Inspect models</button>
            <output>
                {hydrated ? "Hydrated" : "Unhydrated"}; other: {ProjectBotScope.Model.getModel("other-scope") ? "retained" : "deleted"}
            </output>
            <MemoryRouter>
                <BoardProvider project={project} currentUser={user}>
                    <MoreMenu.Root>{<BoardColumnMoreMenuBotList column={column} />}</MoreMenu.Root>
                </BoardProvider>
            </MemoryRouter>
            {opened && <BoardBotScopeList target={{ target_table: "project", target: project }} />}{" "}
            {shared && <BoardBotScopeList target={{ target_table: "project", target: project }} />}
            <ul>
                {requests.map((url, index) => (
                    <li key={index}>{url}</li>
                ))}
            </ul>
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <Fixture />
    </QueryClientProvider>
);
