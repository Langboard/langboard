import "@/core/injection";
import useSocketStore from "@/core/stores/SocketStore";
import { subscribedCallback } from "@/core/stores/socket/registry";
import { ESocketTopic } from "@langboard/core/enums";
import { SocketEvents } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard, MetadataModel } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import useGetCards, { useBoardCardsSnapshot } from "./useGetCards";
import { BoardSnapshotSummary } from "@/pages/BoardPage/components/board/BoardWorkSummary";
import "@/i18n";

const projectUID = "enrichment-fixture";
subscribedCallback(ESocketTopic.Board, [projectUID]);
const actorDate = new Date();
useAuthStore.setState({
    currentUser: AuthUser.Model.fromOne({ uid: "fixture-reader", created_at: actorDate, updated_at: actorDate }),
    state: "loaded",
});
const deny = new URLSearchParams(location.search).has("deny");
let resolveMetadata: () => void = () => {};
let rejectMetadata: () => void = () => {};
let readyMetadata = false;
let metadataReads = 0;
let reportRequest: (url: string) => void = () => {};
let failBoard = false;
let omitWorkState = false;
let holdBoard = false;
let releaseBoard: () => void = () => {};
let reportAbort: () => void = () => {};
api.defaults.adapter = async (config) => {
    reportRequest(config.url!);
    const response = { status: 200, statusText: "OK", headers: {}, config };
    if (config.url!.startsWith("/metadata/")) {
        const version = ++metadataReads;
        config.signal?.addEventListener?.("abort", () => reportAbort());
        if (!readyMetadata)
            await new Promise<void>((resolve, reject) => {
                resolveMetadata = () => {
                    readyMetadata = true;
                    resolve();
                };
                rejectMetadata = () => reject(new AxiosError("Metadata unavailable", "ERR_NETWORK", config));
            });
        return { ...response, data: { metadata: { card: { note: `loaded-${version}` }, removed: { note: "must not return" } } } };
    }
    if (holdBoard)
        await new Promise<void>((resolve) => {
            releaseBoard = resolve;
        });
    if (deny || failBoard) throw new AxiosError("Forbidden", "ERR_BAD_REQUEST", config, undefined, { ...response, status: 403, data: {} });
    const base = { created_at: new Date(), updated_at: new Date() };
    return {
        ...response,
        data: {
            cards: [
                {
                    ...base,
                    uid: "card",
                    project_uid: projectUID,
                    project_column_uid: "column",
                    title: "Authorized card",
                    member_uids: ["fixture-reader"],
                    description: "",
                    order: 0,
                    ...(omitWorkState
                        ? {}
                        : {
                              work_state: {
                                  version: 1,
                                  lifecycle: "active",
                                  material_kind: "work",
                                  completed: false,
                                  workflow_stage: "review",
                                  blocker_state: null,
                                  active_queue_policy: "include",
                                  active_queue_eligible: null,
                              },
                          }),
                },
            ],
            columns: [{ ...base, uid: "column", project_uid: projectUID, name: "Board column", order: 0 }],
            checklists: [],
            global_relationships: [],
            column_bot_scopes: [],
            column_bot_schedules: [],
        },
    };
};
function SecondObserver({ enabled }: { enabled: boolean }) {
    useGetCards({ project_uid: projectUID }, { enabled });
    return null;
}
function Fixture() {
    const queryClient = useQueryClient();
    const [requests, setRequests] = useState<string[]>([]);
    const [secondEnabled, setSecondEnabled] = useState(false);
    const [aborted, setAborted] = useState(0);
    reportAbort = () => setAborted((value) => value + 1);
    reportRequest = (url) => setRequests((value) => [...value, url]);
    const [boardUID, setBoardUID] = useState(projectUID);
    const result = useGetCards({ project_uid: boardUID });
    const snapshot = useBoardCardsSnapshot(boardUID);
    const cards = ProjectCard.Model.useModels((card) => card.project_uid === projectUID);
    const metadata = MetadataModel.Model.useModels(() => true);
    return (
        <>
            <BoardSnapshotSummary snapshot={snapshot} cards={cards} />
            <button
                onClick={() => {
                    const base = { created_at: new Date(), updated_at: new Date() };
                    ProjectCard.Model.fromOne(
                        {
                            ...base,
                            uid: "injected",
                            project_uid: projectUID,
                            project_column_uid: "column",
                            title: "Realtime cache card",
                            member_uids: ["fixture-reader", "second-reader"],
                            description: "",
                            order: 1,
                            work_state: {
                                version: 1,
                                lifecycle: "active",
                                material_kind: "work",
                                completed: false,
                                workflow_stage: "review",
                                blocker_state: "blocked",
                                active_queue_eligible: false,
                            },
                        } as never,
                        true
                    );
                }}
            >
                Inject cache card
            </button>
            <button
                onClick={() => {
                    ProjectCard.Model.getModel("card")!.member_uids = ["second-reader"];
                }}
            >
                Assign second actor
            </button>
            <button
                onClick={() => {
                    const event = Utils.String.format(SocketEvents.SERVER.BOARD.CARD.ASSIGNED_USERS_UPDATED, { uid: "card" });
                    const listeners = useSocketStore.getState().getStore().subscriptions[ESocketTopic.Board]?.[projectUID]?.[event] ?? {};
                    const handlers = Object.values(listeners).flat();
                    if (handlers.length !== 1) throw new Error("Expected one native assignment handler");
                    for (const handler of handlers) handler({ member_uids: ["fixture-reader"] });
                }}
            >
                Assign current actor by socket
            </button>
            {(
                [
                    "archived",
                    "reference",
                    "unknown lifecycle",
                    "unknown material",
                    "unknown completion",
                    "null readiness",
                    "policy excluded",
                    "blocked",
                ] as const
            ).map((boundary) => (
                <button
                    key={boundary}
                    onClick={() => {
                        const card = ProjectCard.Model.getModel("card")!;
                        card.work_state = {
                            ...card.work_state!,
                            ...(boundary === "archived" ? { lifecycle: "archived" } : {}),
                            ...(boundary === "reference" ? { material_kind: "reference" } : {}),
                            ...(boundary === "unknown lifecycle" ? { lifecycle: null } : {}),
                            ...(boundary === "unknown material" ? { material_kind: null } : {}),
                            ...(boundary === "unknown completion" ? { completed: null } : {}),
                            ...(boundary === "null readiness" ? { active_queue_eligible: null } : {}),
                            ...(boundary === "policy excluded" ? { active_queue_policy: "exclude", active_queue_eligible: false } : {}),
                            ...(boundary === "blocked" ? { blocker_state: "blocked", active_queue_eligible: false } : {}),
                        } as never;
                    }}
                >
                    Set {boundary} work
                </button>
            ))}
            <button
                onClick={() => {
                    ProjectCard.Model.getModel("card")!.work_state = {
                        ...ProjectCard.Model.getModel("card")!.work_state!,
                        workflow_stage: "active",
                        blocker_state: "blocked",
                    };
                }}
            >
                Edit work state
            </button>
            <button
                onClick={() => {
                    ProjectCard.Model.getModel("card")!.work_state = { ...ProjectCard.Model.getModel("card")!.work_state!, completed: true };
                }}
            >
                Complete authoritative state
            </button>
            <button
                onClick={() => {
                    ProjectCard.Model.deleteModel("card");
                }}
            >
                Remove snapshot card
            </button>
            <button
                onClick={() => {
                    ProjectCard.Model.getModel("card")!.work_state = undefined;
                }}
            >
                Clear work state
            </button>
            <button
                onClick={() => {
                    holdBoard = true;
                    useAuthStore.setState({
                        currentUser: AuthUser.Model.fromOne({ uid: "second-reader", created_at: actorDate, updated_at: actorDate }),
                    });
                }}
            >
                Swap actor
            </button>
            <button
                onClick={() => {
                    holdBoard = true;
                    const version = useAuthStore.getState().getSessionVersion() + 1;
                    useAuthStore.setState({ getSessionVersion: () => version });
                }}
            >
                Swap session
            </button>
            <button
                onClick={() => {
                    holdBoard = true;
                    setBoardUID("second-board");
                }}
            >
                Swap board
            </button>
            <button
                onClick={() => {
                    holdBoard = true;
                    void queryClient.invalidateQueries({ queryKey: [`get-cards-${boardUID}`] });
                }}
            >
                Invalidate snapshot
            </button>
            <button
                onClick={() => {
                    failBoard = true;
                    void result.refetch();
                }}
            >
                Fail refresh
            </button>
            <button
                onClick={() => {
                    holdBoard = false;
                    releaseBoard();
                }}
            >
                Release board
            </button>
            <button
                onClick={() => {
                    omitWorkState = true;
                    void result.refetch();
                }}
            >
                Refresh without work state
            </button>
            <SecondObserver enabled={secondEnabled} />
            <h1>{result.isError ? "Board denied" : result.data ? "Board ready" : "Board loading"}</h1>
            {cards.map((card) => (
                <p key={card.uid}>{card.title}</p>
            ))}
            <p>Metadata entries: {metadata.length}</p>
            <p>Aborted metadata: {aborted}</p>
            <p>Metadata value: {metadata[0]?.metadata.note ?? "none"}</p>
            <button onClick={() => setSecondEnabled(true)}>Enable second observer</button>
            <button onClick={() => rejectMetadata()}>Reject metadata</button>
            <button onClick={() => resolveMetadata()}>Release metadata</button>
            <button
                onClick={() => {
                    readyMetadata = true;
                    void result.refetch();
                }}
            >
                Refresh board
            </button>
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
