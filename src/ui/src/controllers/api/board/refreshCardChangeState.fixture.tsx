import { createRoot } from "react-dom/client";
import { useState } from "react";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { AuthUser, ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";
import { queryClient } from "@/core/helpers/QueryClient";
import { cancelCardChangeState, refreshCardChangeState } from "./refreshCardChangeState";

const projectUID = "count-refresh-fixture";
const cardUID = "count-refresh-card";
const card = ProjectCard.Model.fromOne({
    uid: cardUID,
    project_uid: projectUID,
    title: "Count fixture",
    created_at: new Date(),
    updated_at: new Date(),
    last_change_seq: 10,
    has_unread_change: false,
    unread_change_count: 0,
    unread_change_count_status: "exact",
});
const actor = (uid: string) => AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser: actor("count-reader-a"), state: "loaded" });
let nextCount = 1;
let hold = false;
let legacy = false;
let release = () => {};
let requests = 0;
api.defaults.adapter = async (config) => {
    requests++;
    const data = legacy
        ? { readers: [] }
        : {
              card_uid: cardUID,
              readers: [],
              change_state: {
                  has_unread_change: true,
                  unread_change_count: nextCount,
                  unread_change_count_status: "exact",
                  latest_change: { seq: 10 + nextCount, target_type: "comment", target_uid: null, at: new Date().toISOString() },
              },
          };
    legacy = false;
    if (hold) {
        hold = false;
        await new Promise<void>((resolve) => {
            release = resolve;
        });
    }
    return { data, status: 200, statusText: "OK", headers: {}, config };
};
function Fixture() {
    const count = card.useField("unread_change_count");
    const [settled, setSettled] = useState(0);
    const board = useQuery({
        queryKey: [`get-cards-${projectUID}`, { project_uid: projectUID }, "count-reader-a", 0],
        queryFn: async () => Date.now(),
        staleTime: Infinity,
    });
    return (
        <main>
            <h1>Single card count refresh</h1>
            <p>Count: {count}</p>
            <p>Settled: {settled}</p>
            <p>Requests: {requests}</p>
            <p>Board invalidated: {String(board.isFetching)}</p>
            <button
                onClick={() => {
                    void refreshCardChangeState(projectUID, cardUID).then(() => setSettled((n) => n + 1));
                }}
            >
                Refresh
            </button>
            <button
                onClick={() => {
                    hold = true;
                }}
            >
                Hold next
            </button>
            <button
                onClick={() => {
                    nextCount = 5;
                }}
            >
                New count
            </button>
            <button onClick={() => release()}>Release held</button>
            <button
                onClick={() => {
                    useAuthStore.setState({ currentUser: actor("count-reader-b") });
                }}
            >
                Change actor
            </button>
            <button
                onClick={() => {
                    void cancelCardChangeState(projectUID, cardUID);
                    ProjectCard.Model.fromOne<typeof ProjectCard.Model, ProjectCard.Interface>({
                        uid: card.uid,
                        has_unread_change: false,
                        unread_change_count: 0,
                    });
                }}
            >
                Seen
            </button>
            <button
                onClick={() => {
                    legacy = true;
                }}
            >
                Legacy response
            </button>
            <output data-board-updated>{board.dataUpdatedAt}</output>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={queryClient}>
        <Fixture />
    </QueryClientProvider>
);
