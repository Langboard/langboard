import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, ProjectCard } from "@/core/models";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { AuthProvider } from "@/core/providers/AuthProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { getSocketMap } from "@/core/stores/socket/state";
import { SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import BoardCommentList from "@/pages/BoardPage/components/card/comment/BoardCommentList";
import "@/i18n";
import "@/core/injection";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

const now = new Date();
await i18n.changeLanguage("en-US");
const user = AuthUser.Model.fromOne({
    uid: "timeline-user",
    type: "user",
    firstname: "Timeline",
    lastname: "Reader",
    email: "timeline@example.invalid",
    created_at: now,
    updated_at: now,
});
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
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Fixture() {
    const [index, setIndex] = useState(0);
    const ref = useRef<HTMLDivElement>(null);
    const location = useLocation();
    const emitMove = () => {
        const event = SocketEvents.SERVER.DASHBOARD.PROJECT.ACTIVITY_RECORDED.replace("{uid}", "timeline-project");
        const callbacks = getSocketMap().subscriptions[ESocketTopic.Dashboard]?.["timeline-project"]?.[event] ?? {};
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
    return (
        <>
            <button onClick={() => setIndex((value) => 1 - value)}>Switch timeline card</button>
            <button onClick={emitMove}>Committed activity</button>
            <button onClick={emitOpen}>Socket reopened</button>
            <output aria-label="Current route">{location.pathname}</output>
            <div ref={ref} className="mx-auto max-w-xl p-4">
                <BoardCardProvider key={cards[index].uid} projectUID="timeline-project" card={cards[index]} currentUser={user} viewportRef={ref}>
                    <BoardCommentList />
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
