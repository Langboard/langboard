import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BoardLoadError from "@/pages/BoardPage/BoardLoadError";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import useGetProject from "./useGetProject";
import useGetCards from "./useGetCards";
import "@/i18n";

const uid = "project-load-fixture";
const delayed = new URLSearchParams(location.search).get("delayed");
let releasePrevious: () => void = () => {};
let reportAbort: () => void = () => {};
const unavailable = new URLSearchParams(location.search).has("unavailable");
let projectReads = 0;
let finishVisit: (fail?: boolean) => void = () => {};
const visitMode = new URLSearchParams(location.search).get("visit");
const deny = new URLSearchParams(location.search).has("deny");
let release: () => void = () => {};
let reject: () => void = () => {};
let reportRequest: (url: string) => void = () => {};
const base = { created_at: new Date(), updated_at: new Date() };
useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...base, uid: "fixture-reader" }), state: "loaded" });
api.defaults.adapter = async (config) => {
    reportRequest(config.url!);
    const response = { status: 200, statusText: "OK", headers: {}, config };
    const requestUID = config.url!.split("/")[2];
    if (
        requestUID === uid &&
        ((delayed === "project" && config.url === `/board/${uid}`) || (delayed === "cards" && config.url!.endsWith("/cards")))
    ) {
        config.signal?.addEventListener?.("abort", () => reportAbort());
        await new Promise<void>((resolve) => {
            releasePrevious = resolve;
        });
    }
    if (config.url!.endsWith("/visit")) {
        if (config.method !== "post") throw new Error("Visit must be an explicit POST");
        if (visitMode === "pending") {
            await new Promise<void>((resolve, rejectVisit) => {
                finishVisit = (fail) => (fail ? rejectVisit(new AxiosError("Visit unavailable", "ERR_NETWORK", config)) : resolve());
            });
        }
        return { ...response, data: {} };
    }
    if (config.url!.endsWith("/dock")) {
        await new Promise<void>((resolve, fail) => {
            release = resolve;
            reject = () => fail(new AxiosError("Dock unavailable", "ERR_NETWORK", config));
        });
        return { ...response, data: { revision: 1, column_uids: [] } };
    }
    if (config.url!.includes("/metadata/")) return { ...response, data: { metadata: {} } };
    if (config.url!.endsWith("/cards"))
        return {
            ...response,
            data: { cards: [], columns: [], checklists: [], global_relationships: [], column_bot_scopes: [], column_bot_schedules: [] },
        };
    if (unavailable && ++projectReads === 1) throw new AxiosError("Canceled transport", "ERR_CANCELED", config);
    if (deny) throw new AxiosError("Forbidden", "ERR_BAD_REQUEST", config, undefined, { ...response, status: 403, data: {} });
    return {
        ...response,
        data: {
            project: { ...base, uid: requestUID, title: "Authorized project", dock_revision: 0 },
            project_bot_scopes: [],
            project_bot_schedules: [],
        },
    };
};
function Cards({ uid }: { uid: string }) {
    const result = useGetCards({ project_uid: uid });
    return <p>{result.isSuccess ? "Cards ready" : "Cards loading"}</p>;
}
function DockRevision({ project }: { project: Project.TModel }) {
    const revision = project.useField("dock_revision");
    return <p>Dock revision: {revision}</p>;
}
function Fixture() {
    const [requests, setRequests] = useState<string[]>([]);
    const [projectUID, setProjectUID] = useState(uid);
    const [aborted, setAborted] = useState(0);
    reportAbort = () => setAborted((value) => value + 1);
    reportRequest = (url) => setRequests((all) => [...all, url]);
    const result = useGetProject({ uid: projectUID });
    return (
        <>
            <h1>{result.isError ? "Project denied" : result.data ? "Project ready" : "Project loading"}</h1>
            {result.error && <BoardLoadError isFetching={result.isFetching} retry={() => void result.refetch()} />}
            {result.data && <Cards uid={projectUID} />}
            {result.data && <DockRevision project={result.data.project} />}
            <button onClick={() => finishVisit(true)}>Reject visit</button>
            <button onClick={() => useAuthStore.getState().removeToken()}>Sign out</button>
            <button onClick={() => release()}>Release dock</button>
            <button onClick={() => reject()}>Reject dock</button>
            <button onClick={() => void result.refetch()}>Refresh project</button>
            <button onClick={() => setProjectUID("next-board")}>Switch board</button>
            <button onClick={() => releasePrevious()}>Release previous request</button>
            <p>Aborted primary requests: {aborted}</p>
            <p>Loaded project: {result.data?.project.uid ?? "none"}</p>
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
