import { createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { queryClient } from "@/core/helpers/QueryClient";
import { AuthUser, Project, ProjectCard, ProjectColumn, ProjectChecklist, GlobalRelationshipType, MetadataModel } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import useGetProject from "./useGetProject";
import useGetCards from "./useGetCards";
import { useCardFlipStore } from "@/pages/BoardPage/components/card/CardFlipStore";
import "@/i18n";
const settings = new URLSearchParams(location.search);
const requests: string[] = [];
const metadataScopes: unknown[] = [];
let omitted = false;
const completed: { kind: string; aborted: boolean }[] = [];
const releases: (() => void)[] = [];
const base = { created_at: new Date(), updated_at: new Date() };
const actor = (uid: string) => AuthUser.Model.fromOne({ ...base, uid } as AuthUser.Interface);
useAuthStore.setState({ currentUser: actor("actor-a"), state: "loaded" });
// Only transport is mocked. Ignore abort deliberately to exercise retained responses.
api.get = (async (url: string, config?: { signal?: AbortSignal; params?: Record<string, unknown> }) => {
    requests.push(url);
    const owner = useAuthStore.getState().currentUser?.uid;
    const kind = url.includes("/metadata/") ? "metadata" : url.endsWith("/cards") ? "cards" : url.endsWith("/dock") ? "dock" : "project";
    if (kind === "metadata") metadataScopes.push(config?.params?.board_visible_only);
    if (kind === settings.get("delay") && owner === "actor-a" && !url.includes("next-board"))
        await new Promise<void>((resolve) => releases.push(resolve));
    completed.push({ kind, aborted: config?.signal?.aborted ?? false });
    const uid = url.split("/")[2];
    const wrong = settings.get("wrong");
    if (kind === "metadata") return { data: { metadata: { card: { note: owner } } } };
    if (kind === "dock") return { data: { revision: 0, column_uids: [] } };
    if (kind === "project")
        return { data: { project: { ...base, uid: wrong === "project" ? "foreign" : uid, title: owner, current_auth_role_actions: ["read"] } } };
    if (omitted) return { data: { cards: [], columns: [], checklists: [], global_relationships: [] } };
    return {
        data: {
            cards: [
                {
                    ...base,
                    uid: "card",
                    project_uid: wrong === "card" ? "foreign" : uid,
                    project_column_uid: "column",
                    title: owner,
                    labels: wrong === "label" ? [{ ...base, uid: "foreign-label", project_uid: "foreign", name: "Foreign" }] : [],
                    relationships:
                        wrong === "relationship" ? [{ ...base, uid: "foreign-relation", parent_card_uid: "other", child_card_uid: "foreign" }] : [],
                    description: "",
                    order: 0,
                },
            ],
            columns: [{ ...base, uid: "column", project_uid: wrong === "column" ? "foreign" : uid, name: "Column", order: 0 }],
            checklists: [
                {
                    ...base,
                    uid: "checklist",
                    card_uid: wrong === "checklist" ? "foreign-card" : "card",
                    title: "Checklist",
                    checkitems: wrong === "checkitem" ? [{ ...base, uid: "foreign-item", card_uid: "foreign", checklist_uid: "checklist" }] : [],
                    order: 0,
                },
            ],
            global_relationships: [{ ...base, uid: "relation", parent_name: "Parent", child_name: "Child", description: "" }],
        },
    };
}) as typeof api.get;
function Fixture() {
    const [board, setBoard] = useState("board");
    const project = useGetProject({ uid: board }, { staleTime: Infinity });
    const cards = useGetCards({ project_uid: board }, { staleTime: Infinity, enabled: !settings.has("disabled") });
    Object.assign(window, {
        hydration: {
            requests,
            metadataScopes,
            completed,
            retain: () =>
                queryClient
                    .getQueryCache()
                    .getAll()
                    .forEach((query) => {
                        const observer = new QueryObserver(queryClient, { ...query.options, queryKey: query.queryKey, enabled: false });
                        observer.subscribe(() => {});
                    }),
            release: () => releases.splice(0).forEach((release) => release()),
            switchActor: () => useAuthStore.setState({ currentUser: actor("actor-b") }),
            logout: () => useAuthStore.getState().removeToken(),
            switchBoard: () => setBoard("next-board"),
            omit: () => {
                useCardFlipStore.getState().flip("actor-a", "board", { uid: "card", title: "Card" });
                omitted = true;
                void cards.refetch();
            },
            refresh: () => {
                void project.refetch();
                void cards.refetch();
            },
            snapshot: () => ({
                project: Project.Model.getModel("board")?.title,
                card: ProjectCard.Model.getModel("card")?.title,
                flipped: useCardFlipStore.getState().trays["actor-a:board"]?.some((card) => card.uid === "card") ?? false,
                cardBoard: ProjectCard.Model.getModel("card")?.project_uid,
                metadata: MetadataModel.Model.getModel("card")?.metadata.note,
                foreign: !!Project.Model.getModel("foreign"),
                columns: ProjectColumn.Model.getModels(() => true).length,
                checklists: ProjectChecklist.Model.getModels(() => true).length,
                relations: GlobalRelationshipType.Model.getModels(() => true).length,
                projectStatus: project.status,
                cardsStatus: cards.status,
            }),
        },
    });
    return null;
}
createRoot(document.getElementById("root")!).render(createElement(QueryClientProvider, { client: queryClient }, createElement(Fixture)));
