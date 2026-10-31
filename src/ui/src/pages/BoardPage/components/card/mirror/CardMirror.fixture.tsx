import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { MemoryRouter } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/core/helpers/QueryClient";
import { api } from "@/core/helpers/Api";
import { AuthUser, Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import useSocketStore from "@/core/stores/SocketStore";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider, useSocket } from "@/core/providers/SocketProvider";
import { BoardProvider, useBoard } from "@/core/providers/BoardProvider";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import useGetCards from "@/controllers/api/board/useGetCards";
import BoardColumnCard from "@/pages/BoardPage/components/board/BoardColumnCard";
import { installNativeSocketFixture } from "../../board/wiki-summary-tests/nativeSocket.fixture";
import { ESocketTopic } from "@langboard/core/enums";
import i18n from "@/i18n";
import "@/core/injection";
import "@/assets/styles/main.css";

api.defaults.baseURL = location.origin;
const now = new Date();
const user = AuthUser.Model.fromOne({
    uid: "fixture-user",
    created_at: now,
    updated_at: now,
    is_admin: true,
    preferred_lang: "en-US",
});
useAuthStore.setState({
    currentUser: user,
    state: "loaded",
    pageLoaded: true,
    hydratedSessionVersion: 1,
    getToken: () => "fixture-token",
    getSessionVersion: () => 1,
});
installNativeSocketFixture();
const wire = useSocketStore.getState().getSocket()!;
useSocketStore.setState({ createSocket: () => wire });

const projects = ["source", "destination"].map((uid) =>
    Project.Model.fromOne({
        uid,
        title: `${uid} board`,
        created_at: now,
        updated_at: now,
        current_auth_role_actions: ["*"],
        archive_visible_days: 30,
        all_members: [user],
    } as never)
);

function ClosedCards() {
    const { cards, project, filters } = useBoard();
    return (
        <>
            <output data-pane-filter={project.uid}>{JSON.stringify(filters)}</output>
            <div data-pane-scroll={project.uid} style={{ height: 240, overflow: "auto" }}>
                <div style={{ minHeight: 600 }}>
                    {cards.map((card) => (
                        <BoardColumnCard key={card.uid} card={card} />
                    ))}
                </div>
            </div>
        </>
    );
}

function Pane({ project }: { project: Project.TModel }) {
    const socket = useSocket();
    const result = useGetCards({ project_uid: project.uid });
    useEffect(() => {
        socket.subscribe(ESocketTopic.Board, [project.uid]);
        return () => socket.unsubscribe(ESocketTopic.Board, [project.uid]);
    }, [socket, project]);
    return (
        <section aria-label={`${project.uid} pane`}>
            {result.isSuccess ? (
                <BoardProvider project={project} currentUser={user}>
                    <ClosedCards />
                </BoardProvider>
            ) : (
                <output>{result.status}</output>
            )}
        </section>
    );
}

function Fixture() {
    const [mounted, setMounted] = useState(true);
    return (
        <>
            <button className="btn" onClick={() => setMounted((value) => !value)}>
                Toggle panes
            </button>
            {mounted
                ? projects.map((project) => (
                      <PageNavigationScope
                          key={project.uid}
                          basePath={`/board/${project.uid}`}
                          initialEntry={`/board/${project.uid}?filters=columns:${project.uid}-column`}
                      >
                          <Pane project={project} />
                      </PageNavigationScope>
                  ))
                : null}
        </>
    );
}

void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <MemoryRouter initialEntries={["/workspace"]}>
            <QueryClientProvider client={queryClient}>
                <PageHeaderProvider>
                    <AuthProvider>
                        <SocketProvider>
                            <Fixture />
                        </SocketProvider>
                    </AuthProvider>
                </PageHeaderProvider>
            </QueryClientProvider>
        </MemoryRouter>
    )
);
