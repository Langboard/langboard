import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { lazy, Suspense, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useAuthStore from "@/core/stores/AuthStore";
const BoardAutomationRuns = lazy(() => import("@/pages/BoardPage/BoardAutomationRuns"));
const base = { created_at: new Date(), updated_at: new Date() };
const boards = ["left", "right"].map((uid) => Project.Model.fromOne({ ...base, uid, title: uid, current_auth_role_actions: ["read", "update"] }));
useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...base, uid: "reader" }), state: "loaded" });
let session = 0;
function Fixture() {
    const [open, setOpen] = useState(false);
    const [board, setBoard] = useState(0);
    return (
        <main className="p-3">
            <button onClick={() => setOpen(!open)}>Toggle automation</button> <button onClick={() => setBoard(1 - board)}>Switch board</button>{" "}
            <button onClick={() => useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...base, uid: "other-reader" }) })}>
                Switch actor
            </button>{" "}
            <button
                onClick={() => {
                    session++;
                    useAuthStore.setState({ getSessionVersion: () => session });
                }}
            >
                Switch session
            </button>{" "}
            <button
                onClick={() => {
                    boards[board].current_auth_role_actions = [ProjectRole.EAction.Read];
                }}
            >
                Switch permissions
            </button>{" "}
            <button
                onClick={() => {
                    boards[board].current_auth_role_actions = [];
                }}
            >
                Revoke
            </button>
            {open && (
                <Suspense fallback={<p>Loading module</p>}>
                    <BoardAutomationRuns project={boards[board]} />
                </Suspense>
            )}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <Fixture />
    </QueryClientProvider>
);
