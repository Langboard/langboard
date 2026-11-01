import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/core/helpers/QueryClient";
import useCardRelationshipsUpdatedHandlers from "@/controllers/socket/card/useCardRelationshipsUpdatedHandlers";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { ESocketTopic } from "@langboard/core/enums";
import { installNativeSocketFixture } from "../wiki-summary-tests/nativeSocket.fixture";
import { createSocketRuntime } from "@/core/providers/socket/runtime";
import useSocketStore from "@/core/stores/SocketStore";
import useGetCardRelationSummaries from "@/controllers/api/card/useGetCardRelationSummaries";
import useGetWikiSummaries from "@/controllers/api/wiki/useGetWikiSummaries";
import { AuthUser, ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import BoardRelationsSidebar from "../BoardRelationsSidebar";
import "@/i18n";
import "@/assets/styles/main.css";
import "@/core/injection";

function selectActor(uid: string) {
    useAuthStore.setState({
        state: "loaded",
        pageLoaded: true,
        currentUser: AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() }),
    });
}
selectActor("actor-a");
const reconnectFixture = new URLSearchParams(location.search).has("reconnect");
if (reconnectFixture) {
    // Replace only the wire; native transport owns disconnect, reconnect, open and subscription acknowledgements.
    class FixtureSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        static all: FixtureSocket[] = [];
        readyState = FixtureSocket.CONNECTING;
        onopen: ((event: Event) => unknown) | null = null;
        onmessage: ((event: { data: string }) => unknown) | null = null;
        onclose: ((event: { code: number }) => unknown) | null = null;
        onerror: ((event: Event) => unknown) | null = null;
        opened: Promise<void>;
        constructor() {
            FixtureSocket.all.push(this);
            this.opened = new Promise<void>((resolve) =>
                queueMicrotask(async () => {
                    this.readyState = FixtureSocket.OPEN;
                    await this.onopen?.(new Event("open"));
                    resolve();
                })
            );
        }
        send(raw: string) {
            const frame = JSON.parse(raw);
            if (frame.event === "subscribe" || frame.event === "unsubscribe") {
                queueMicrotask(() =>
                    this.onmessage?.({ data: JSON.stringify({ ...frame, event: frame.event === "subscribe" ? "subscribed" : "unsubscribed" }) })
                );
            }
        }
        close() {
            this.readyState = FixtureSocket.CLOSED;
        }
        async disconnect() {
            this.readyState = FixtureSocket.CLOSED;
            await this.onclose?.({ code: 1006 });
        }
    }
    window.WebSocket = FixtureSocket as unknown as typeof WebSocket;
    const store = useSocketStore.getState();
    const runtime = createSocketRuntime({
        createSocket: store.createSocket,
        getStore: store.getStore,
        closeSocket: store.close,
        getAccessToken: () => "reconnect-fixture-token",
        getUserUID: () => useAuthStore.getState().currentUser?.uid,
        removeAccessToken: () => {},
        reconnect: () => runtime.connect(),
        shouldReconnect: () => !!useAuthStore.getState().currentUser,
    });
    runtime.connect();
    Object.assign(window, {
        relationReconnect: {
            disconnect: () => FixtureSocket.all.at(-1)!.disconnect(),
            reconnect: async () => {
                runtime.connect();
                await FixtureSocket.all.at(-1)!.opened;
                return {
                    sockets: FixtureSocket.all.length,
                    summaries: queryClient
                        .getQueryCache()
                        .findAll({
                            predicate: ({ queryKey }) => ["wiki-summaries", "card-relation-summaries"].includes(String(queryKey[0])),
                        })
                        .map(({ queryKey, state }) => ({ queryKey, data: state.data })),
                };
            },
        },
    });
} else installNativeSocketFixture();
useSocketOutsideProvider().subscribe(ESocketTopic.Board, ["home"]);
useCardRelationshipsUpdatedHandlers({ projectUID: "home" }).on();
// An incomplete board snapshot must neither suppress missing targets nor reveal stale private rows.
ProjectCard.Model.fromOne({
    uid: "root",
    project_uid: "home",
    title: "Cached root",
    created_at: new Date(),
    updated_at: new Date(),
    relationships: [],
    work_state: new URLSearchParams(window.location.search).has("blocked")
        ? ({
              dependency_state: {
                  state: "blocked",
                  direct_blockers: [
                      {
                          relationship_uid: "hidden-relationship-uid",
                          card_uid: "private-uid",
                          title: "Secret private title",
                          accessible: false,
                          code: "dependency_unfinished",
                      },
                  ],
              },
          } as ProjectCard.IWorkState)
        : undefined,
});

function DuplicateSummaryConsumers() {
    useGetCardRelationSummaries("home", "root");
    useGetCardRelationSummaries("remote", "remote-root");
    useGetWikiSummaries("home");
    return null;
}
ProjectCard.Model.fromOne({
    uid: "private-uid",
    project_uid: "home",
    title: "Secret private title",
    created_at: new Date(),
    updated_at: new Date(),
    relationships: [],
});

function Fixture() {
    const location = useLocation();
    const [shown, setShown] = useState(false);
    const [selection, setSelection] = useState({ projectUID: "home", cardUID: "root" });
    return (
        <>
            <div className="flex flex-wrap gap-2 p-2">
                <button onClick={() => setShown(!shown)}>{shown ? "Hide Relations" : "Show Relations"}</button>
                <button onClick={() => setSelection({ projectUID: "home", cardUID: "other" })}>Select other card</button>
                <button onClick={() => setSelection({ projectUID: "remote", cardUID: "remote-root" })}>Select other project</button>
                <button onClick={() => selectActor("actor-b")}>Change actor</button>
                <button onClick={() => useAuthStore.getState().removeToken()}>Logout</button>
            </div>
            <output data-testid="location">{location.pathname}</output>
            <output data-testid="missing-cache">{String(!!ProjectCard.Model.getModel("missing"))}</output>
            <aside className="h-[750px] w-[296px] border">{shown && <BoardRelationsSidebar {...selection} />}</aside>
            {shown && reconnectFixture && <DuplicateSummaryConsumers />}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={queryClient}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
