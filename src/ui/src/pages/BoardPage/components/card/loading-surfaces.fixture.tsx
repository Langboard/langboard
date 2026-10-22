import { Suspense, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import Dialog from "@/components/base/Dialog";
import BoardCard from "./BoardCard";
import { BoardCardSectionSaveProvider } from "./BoardCardSectionSaveProvider";
import { CARD_BODY_SECTIONS } from "./CardWorkbenchLenses";
import { SkeletonBoard } from "../board/Board";
import "@/core/injection/StringExtensions";
import "@/i18n";
import "@/assets/styles/main.css";

const params = new URLSearchParams(location.search);
const mode = params.get("mode") ?? "slow";
const date = new Date();
const currentUser = AuthUser.Model.fromOne({ uid: "loading-user", created_at: date, updated_at: date });
useAuthStore.setState({ currentUser });
const cardData = {
    uid: "loading-card",
    project_uid: "loading-project",
    title: "Current execution card",
    created_at: date,
    updated_at: date,
    visibility: "PRIVATE",
    current_auth_role_actions: [],
    count_comment: 0,
};
const client = new QueryClient();
const queryKey = [
    "get-card-details-loading-project-loading-card",
    currentUser.uid,
    useAuthStore.getState().getSessionVersion(),
    [...CARD_BODY_SECTIONS].sort().join(","),
];
if (mode === "cache")
    client.setQueryData(queryKey, {
        card: ProjectCard.Model.fromOne(cardData),
        loaded_sections: CARD_BODY_SECTIONS,
    });
let reads = 0;
let release: ((fail?: boolean) => void) | undefined;
api.defaults.adapter = async (config) => {
    if (!config.url?.endsWith("/card/loading-card")) return { data: { records: [], items: [] }, status: 200, statusText: "OK", config, headers: {} };
    document.querySelector("output")!.textContent = String(++reads);
    const fail =
        mode === "fast"
            ? false
            : await new Promise<boolean>((resolve) => {
                  release = (value = false) => resolve(value);
              });
    if (fail)
        throw new AxiosError("Synthetic unavailable card", "ERR_BAD_RESPONSE", config, undefined, {
            status: 503,
            statusText: "Unavailable",
            data: {},
            config,
            headers: {},
        });
    return { data: { card: cardData }, status: 200, statusText: "OK", config, headers: {} };
};
function Fixture() {
    const ref = useRef<HTMLDivElement>(null);
    const [version, setVersion] = useState(0);
    return (
        <>
            <div className="flex flex-wrap gap-2 p-2">
                <output>0</output>
                <button onClick={() => release?.()}>Resolve load</button>
                <button onClick={() => release?.(true)}>Reject load</button>
                <button onClick={() => void client.invalidateQueries({ queryKey })}>Refresh cache</button>
                <button onClick={() => setVersion(version + 1)}>Render again</button>
            </div>
            {mode === "board" ? (
                <div className="h-[calc(100dvh-80px)]" data-version={version}>
                    <SkeletonBoard />
                </div>
            ) : (
                <Dialog.Root open modal={false}>
                    <Dialog.Content
                        withCloseButton={false}
                        nonModalOverlay
                        className="flex h-[calc(100dvh-100px)] max-h-[900px] w-[calc(100vw-24px)] max-w-5xl flex-col gap-2 overflow-hidden p-0"
                    >
                        <BoardCardSectionSaveProvider>
                            <BoardCard
                                projectUID="loading-project"
                                cardUID="loading-card"
                                currentUser={currentUser}
                                viewportRef={ref}
                                isExpanded={params.has("expanded")}
                            />
                        </BoardCardSectionSaveProvider>
                    </Dialog.Content>
                </Dialog.Root>
            )}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <Suspense>
        <MemoryRouter>
            <QueryClientProvider client={client}>
                <DndProvider backend={HTML5Backend}>
                    <Fixture />
                </DndProvider>
            </QueryClientProvider>
        </MemoryRouter>
    </Suspense>
);
