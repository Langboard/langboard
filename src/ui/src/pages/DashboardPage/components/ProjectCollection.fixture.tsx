import { createRoot } from "react-dom/client";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import i18n from "@/i18n";
import CreateProjectFormDialog from "./CreateProjectFormDialog";
import ProjectCollectionDialog from "./ProjectCollectionDialog";
import useProjectCollections from "./useProjectCollections";
import "@/assets/styles/main.css";
await i18n.changeLanguage("en-US");
const user = (uid: string) => AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser: user("collection-account-a") });
function Fixture() {
    const location = useLocation();
    const native = new URLSearchParams(window.location.search).has("native");
    const keepOpened = new URLSearchParams(window.location.search).has("keep-open-on-account-switch");
    const [actor, setActor] = useState("collection-account-a");
    const [opened, setOpened] = useState(false);
    const [boards, setBoards] = useState([
        { uid: "00000000001", title: "ERP board" },
        { uid: "00000000002", title: "Operations board" },
    ]);
    const result = useProjectCollections(actor);
    return (
        <>
            <button disabled={result.busy || result.error} onClick={() => setOpened(true)}>
                Add collection
            </button>
            <button
                onClick={() => {
                    useAuthStore.setState({ currentUser: user("collection-account-b") });
                    setActor("collection-account-b");
                    if (!keepOpened) setOpened(false);
                }}
            >
                Switch account
            </button>
            <output className="block break-all" data-busy={result.busy}>
                {JSON.stringify(result.collections)}
            </output>
            <span data-path hidden>
                {location.pathname}
            </span>
            {native && <CreateProjectFormDialog opened={opened} setOpened={setOpened} />}
            {!native && opened && (
                <ProjectCollectionDialog
                    key={actor}
                    opened
                    onOpenChange={setOpened}
                    collections={result.collections}
                    save={result.save}
                    boards={boards}
                    refreshBoards={() => setBoards((items) => [...items, { uid: "00000000003", title: "Created source" }])}
                />
            )}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
