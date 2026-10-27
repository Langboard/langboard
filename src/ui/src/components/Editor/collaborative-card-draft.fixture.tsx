// Native card page, providers, sections, Plate/Yjs editor and draft store.
// Identity/API responses and unrelated panels are fixture-local; no deployment proof.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import Button from "@/components/base/Button";
import Toast from "@/components/base/Toast";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard } from "@/core/models";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { ProjectRole } from "@/core/models/roles";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { ROUTES } from "@/core/routing/constants";
import useAuthStore from "@/core/stores/AuthStore";
import useCardDraftStore from "@/core/stores/CardDraftStore";
import { Routing } from "@langboard/core/constants";
import BoardCardPage from "@/pages/BoardPage/BoardCardPage";
import "@/core/injection/StringExtensions";
import "@/i18n";
import "@/assets/styles/main.css";

const projectUID = "collaborative-card-draft-project";
const cardUID = "collaborative-card-draft-card";
const richFixture = new URLSearchParams(window.location.search).get("rich");
const projectMembers = [
    { uid: "rich-draft-alice", type: "user", firstname: "Alice", lastname: "Kim", username: "alice" },
    { uid: "rich-draft-bob", type: "user", firstname: "Bob", lastname: "Lee", username: "bob" },
].map((member) => ({ ...member, created_at: new Date(), updated_at: new Date() }));
let writes = 0;
let failSave = false;
const savedDescriptions: Record<string, string> = {};
let fixtureActorUID = "collaborative-card-draft-user-a";
const makeUser = (uid: string) => AuthUser.Model.fromOne({ uid, preferred_lang: "en", created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser: makeUser("collaborative-card-draft-user-a"), state: "loaded", pageLoaded: true });

api.defaults.adapter = async (config) => {
    const uid = config.url?.includes("collaborative-card-draft-other-card") ? "collaborative-card-draft-other-card" : cardUID;
    let data: unknown = { items: [], comments: [], requests: [], next_cursor: null, target_revision: "fixture" };
    if (config.url === Routing.API.AUTH.ABOUT_ME) {
        data = { user: { uid: fixtureActorUID, preferred_lang: "en", created_at: new Date(), updated_at: new Date() }, bots: [] };
    } else if (config.method === "put") {
        writes++;
        document.querySelector("[data-testid=writes]")!.textContent = String(writes);
        if (failSave) {
            throw new AxiosError("Synthetic save failure", "ERR_BAD_RESPONSE", config, undefined, {
                config,
                data: { detail: "Synthetic save failure" },
                status: 500,
                statusText: "Synthetic failure",
                headers: {},
            });
        }
        const submitted = JSON.parse(config.data ?? "{}");
        if (submitted.description) savedDescriptions[uid] = submitted.description.content;
    } else if (config.url?.endsWith(`/card/${uid}`)) {
        data = {
            card: {
                uid,
                project_uid: projectUID,
                title: "Original title",
                description: { content: savedDescriptions[uid] ?? "Original description" },
                current_auth_role_actions: [ProjectRole.EAction.CardUpdate],
                visibility: richFixture === "public" ? "PUBLIC" : "PRIVATE",
                project_members: richFixture ? projectMembers : [],
                count_comment: 0,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
            },
            attachments: [],
            checklists: [],
            linked_wikis: [],
        };
    }
    return { config, data, status: 200, statusText: "OK", headers: {} };
};

function Fixture() {
    const location = useLocation();
    const navigate = useNavigate();
    const currentUser = useAuthStore((state) => state.currentUser);
    const cache = useCardDraftStore((state) => state.cache);
    const [isExpanded, setIsExpanded] = useState(false);
    const [, setFailure] = useState(false);
    const activeCardUID = location.pathname.endsWith("collaborative-card-draft-other-card") ? "collaborative-card-draft-other-card" : cardUID;
    const open = location.pathname !== ROUTES.BOARD.MAIN(projectUID);
    return (
        <>
            <nav
                data-workbench-sidebar
                className="max-h-24 max-w-[calc(100vw-3.5rem)] overflow-auto"
                style={{ position: "fixed", left: 0, top: 0, zIndex: 1000 }}
            >
                <Button onClick={() => navigate(ROUTES.BOARD.CARD(projectUID, cardUID))}>Reopen card</Button>
                <Button onClick={() => navigate(ROUTES.BOARD.CARD(projectUID, "collaborative-card-draft-other-card"))}>Switch card</Button>
                <Button
                    onClick={async () => {
                        const uid =
                            currentUser?.uid === "collaborative-card-draft-user-a"
                                ? "collaborative-card-draft-user-b"
                                : "collaborative-card-draft-user-a";
                        fixtureActorUID = uid;
                        await useAuthStore.getState().updateToken(`synthetic-${uid}`, api);
                    }}
                >
                    Switch account
                </Button>
                <Button
                    onClick={async () => {
                        fixtureActorUID = currentUser!.uid;
                        await useAuthStore.getState().updateToken("synthetic-rotated-session", api);
                    }}
                >
                    Rotate session
                </Button>
                <Button onClick={() => useAuthStore.getState().removeToken()}>Log out</Button>
                <Button
                    onClick={() => {
                        savedDescriptions[activeCardUID] = "Delayed server content";
                        const activeCard = ProjectCard.Model.getModels((model) => model.uid === activeCardUID)[0];
                        if (activeCard) activeCard.description = { content: "Delayed server content" };
                    }}
                >
                    Delayed server update
                </Button>
                <Button
                    onClick={() => {
                        failSave = !failSave;
                        setFailure(failSave);
                    }}
                >
                    Toggle save failure
                </Button>
                <output data-testid="writes">{writes}</output>
                <output data-testid="route">{location.pathname}</output>
                <output data-testid="actor">{currentUser?.uid}</output>
                <output data-testid="drafts">{JSON.stringify(cache)}</output>
                <output data-testid="saved-body">{savedDescriptions[activeCardUID] ?? ""}</output>
            </nav>
            {!currentUser ? (
                <p>Signed out</p>
            ) : open ? (
                <BoardCardPage projectUID={projectUID} cardUID={activeCardUID} isExpanded={isExpanded} setIsExpanded={setIsExpanded} />
            ) : (
                <p>Card closed</p>
            )}
            <Toast.Area />
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <MemoryRouter initialEntries={[ROUTES.BOARD.CARD(projectUID, cardUID)]}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            <DndProvider backend={HTML5Backend}>
                <AuthProvider>
                    <Fixture />
                </AuthProvider>
            </DndProvider>
        </QueryClientProvider>
    </MemoryRouter>
);
