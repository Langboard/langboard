import axios from "axios";
import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, ProjectCard } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { AuthProvider } from "@/core/providers/AuthProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { getSocketMap } from "@/core/stores/socket/state";
import { SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import CardColumnHistory from "@/pages/BoardPage/components/card/CardColumnHistory";
import BoardCommentList from "@/pages/BoardPage/components/card/comment/BoardCommentList";
import "@/i18n";
import "@/core/injection";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

const now = new Date();
await i18n.changeLanguage("en-US");
const userData = {
    uid: "timeline-user",
    type: "user",
    firstname: "Timeline",
    lastname: "Reader",
    email: "timeline@example.invalid",
    created_at: now,
    updated_at: now,
};
const user = AuthUser.Model.fromOne(userData);
useAuthStore.setState({ currentUser: user, state: "loaded", getToken: () => "card-timeline-local-fixture" });
const cards = ["first-card", "second-card"].map((uid) =>
    ProjectCard.Model.fromOne({
        uid,
        project_uid: "timeline-project",
        project_column_uid: "doing",
        title: "Timeline fixture",
        visibility: "PRIVATE",
        count_comment: 1,
        current_auth_role_actions: ["read", "card_update"],
        created_at: now,
        updated_at: now,
    })
);
const history = new URLSearchParams(window.location.search).has("columnHistory");
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Fixture() {
    const [index, setIndex] = useState(0);
    const [projectUID, setProjectUID] = useState("timeline-project");
    const actor = useAuthStore((state) => state.currentUser);
    const ref = useRef<HTMLDivElement>(null);
    const location = useLocation();
    const emitMove = () => {
        const event = SocketEvents.SERVER.DASHBOARD.PROJECT.ACTIVITY_RECORDED.replace("{uid}", projectUID);
        const callbacks = getSocketMap().subscriptions[ESocketTopic.Dashboard]?.[projectUID]?.[event] ?? {};
        Object.values(callbacks)
            .flat()
            .forEach((callback) => callback({}));
    };
    const emitOpen = () => {
        const map = getSocketMap();
        Object.values(map.defaultEvents.open ?? {})
            .flat()
            .forEach((callback) => callback({}));
    };
    const rotateToken = (uid = userData.uid) =>
        useAuthStore.getState().updateToken(
            "rotated-local-fixture-token",
            axios.create({
                adapter: async (config) => ({ data: { user: { ...userData, uid }, bots: [] }, status: 200, statusText: "OK", headers: {}, config }),
            })
        );
    return (
        <>
            <button onClick={() => setIndex((value) => 1 - value)}>Switch timeline card</button>
            <button onClick={emitMove}>Committed activity</button>
            <button onClick={emitOpen}>Socket reopened</button>
            <button onClick={() => void rotateToken()}>Rotate same user token</button>
            <button onClick={() => void rotateToken("different-timeline-user")}>Change timeline account</button>
            {history && (
                <>
                    <button onClick={() => setProjectUID((value) => (value === "timeline-project" ? "other-project" : "timeline-project"))}>
                        Switch history project
                    </button>
                    <button
                        onClick={() => {
                            cards[index].current_auth_role_actions = [ProjectRole.EAction.CardUpdate];
                        }}
                    >
                        Revoke history read
                    </button>
                    <button
                        onClick={() => {
                            cards[index].current_auth_role_actions = [ProjectRole.EAction.Read, ProjectRole.EAction.CardUpdate];
                        }}
                    >
                        Restore history read
                    </button>
                    <button onClick={() => useAuthStore.getState().removeToken()}>Sign out history user</button>
                </>
            )}
            <output aria-label="Current route">{location.pathname}</output>
            <div ref={ref} className="mx-auto max-w-xl p-4">
                <BoardCardProvider
                    key={history ? `${actor?.uid}:${projectUID}:${cards[index].uid}` : cards[index].uid}
                    projectUID={projectUID}
                    card={cards[index]}
                    currentUser={history ? (actor ?? user) : user}
                    viewportRef={ref}
                >
                    {history ? <CardColumnHistory card={cards[index]} /> : <BoardCommentList />}
                </BoardCardProvider>
            </div>
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={client}>
        <MemoryRouter>
            <AuthProvider>
                <SocketProvider>
                    <Fixture />
                </SocketProvider>
            </AuthProvider>
        </MemoryRouter>
    </QueryClientProvider>
);
