import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient as client } from "@/core/helpers/QueryClient";
import { installNativeSocketFixture } from "./nativeSocket.fixture";
import { acquireWorkspaceSummaryTopics } from "@/controllers/api/WorkspaceSummaryInvalidation";
import useBoardWikiCreatedHandlers from "@/controllers/socket/wiki/useBoardWikiCreatedHandlers";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { ESocketTopic } from "@langboard/core/enums";
import BoardWikiSidebar from "@/pages/BoardPage/components/board/BoardWikiSidebar";
import { AuthUser, ProjectWiki, ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

api.defaults.baseURL = location.origin;
let session = 0;
function setActor(uid: string) {
    const version = ++session;
    useAuthStore.setState({
        state: "loaded",
        currentUser: AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() }),
        getSessionVersion: () => version,
        getToken: () => `fixture-${uid}-${version}`,
    });
}
setActor("reader-a");
installNativeSocketFixture();
Object.assign(window, {
    summaryModelCounts: () => ({ wikis: ProjectWiki.Model.getModels(() => true).length, cards: ProjectCard.Model.getModels(() => true).length }),
});

function Fixture() {
    const route = useLocation();
    const [project, setProject] = useState("project-a");
    const [panel, setPanel] = useState(!new URLSearchParams(window.location.search).has("inactive"));
    const [nativeOwner, setNativeOwner] = useState(new URLSearchParams(window.location.search).has("legacy"));
    const [navigations, setNavigations] = useState(0);
    const actor = useAuthStore((state) => state.currentUser?.uid);
    useEffect(() => {
        if (!nativeOwner) return;
        const off = useBoardWikiCreatedHandlers({ projectUID: project }).on();
        const release = acquireWorkspaceSummaryTopics(useSocketOutsideProvider(), [[ESocketTopic.BoardWiki, project]]);
        return () => {
            off();
            release();
        };
    }, [nativeOwner, project]);
    return (
        <main className="flex h-screen flex-col gap-2 p-2">
            <div className="flex flex-wrap gap-2">
                <button className="btn btn-sm" onClick={() => setActor("reader-b")}>
                    Switch actor
                </button>
                <button className="btn btn-sm" onClick={() => setActor("reader-a")}>
                    Return actor
                </button>
                <button className="btn btn-sm" onClick={() => setActor(actor ?? "reader-a")}>
                    Rotate session
                </button>
                <button className="btn btn-sm" onClick={() => useAuthStore.getState().removeToken()}>
                    Log out
                </button>
                <button className="btn btn-sm" onClick={() => setProject("project-b")}>
                    Switch project
                </button>
                <button className="btn btn-sm" onClick={() => setProject("project-a")}>
                    Return project
                </button>
                <button className="btn btn-sm" onClick={() => void client.invalidateQueries({ queryKey: ["wiki-summaries"] })}>
                    Refresh
                </button>
                <button className="btn btn-sm" onClick={() => setPanel((value) => !value)}>
                    Toggle panel
                </button>
                <button className="btn btn-sm" onClick={() => setNativeOwner((value) => !value)}>
                    Toggle native owner
                </button>
            </div>
            <output data-testid="route">{route.pathname}</output>
            <output data-testid="navigations">{navigations}</output>
            <output data-testid="actor">{actor ?? "logged-out"}</output>
            <div className="min-h-0 w-full max-w-80 flex-1 border">
                {panel ? <BoardWikiSidebar projectUID={project} onNavigate={() => setNavigations((value) => value + 1)} /> : null}
            </div>
        </main>
    );
}

void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={["/board/project-a"]}>
                <Fixture />
            </MemoryRouter>
        </QueryClientProvider>
    )
);
