import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, ProjectCard } from "@/core/models";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import CardRow from "@/pages/DashboardPage/components/CardRow";
import BoardCardMirror from "./BoardCardMirror";
import "@/assets/styles/main.css";
import "@/i18n";
import "@/core/injection/StringExtensions";

const mode = new URLSearchParams(location.search).get("mode");
const card = new ProjectCard.Model({
    uid: "destination-mirror",
    project_uid: "destination-board",
    source_type: "project_card",
    project_column_name: "Destination column",
    created_at: new Date(),
    updated_at: new Date(),
    title: "UNTRUSTED LOCAL TITLE",
    can_delete: mode !== "viewer",
    current_auth_role_actions: mode === "viewer" ? ["read"] : ["card_update", "card_delete"],
    linked_resource: {
        type: "project_card",
        uid: "source-card",
        project_uid: "source-board",
        url: mode === "bad-url" ? "https://example.com" : "/board/source-board/source-card",
        title: "Authorized source title",
        status: mode === "forbidden" ? "forbidden" : mode === "missing" ? "missing" : "available",
        content: { content: "Authorized description", content_type: "markdown" },
        preview: "Authorized preview",
        allowed_actions: ["open_original", "remove_mirror"],
    },
});
const user = new AuthUser.Model({ uid: "fixture-user" });
api.defaults.adapter = async (config) => {
    document.querySelector("[data-request]")!.textContent = `${config.method} ${config.url}`;
    return { config, status: 200, statusText: "OK", headers: {}, data: {} };
};
function Probe() {
    return (
        <>
            <output data-location>{useLocation().pathname}</output>
            <output data-request />
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/board/destination-board/destination-mirror"]}>
            <Probe />
            <Dialog.Root open>
                <Dialog.Content aria-describedby="" className="h-[600px]">
                    <BoardCardProvider projectUID="destination-board" card={card} currentUser={user} viewportRef={{ current: null }}>
                        <table data-dashboard-card>
                            <tbody>
                                <CardRow card={card} />
                            </tbody>
                        </table>
                        <BoardCardMirror isExpanded={false} />
                    </BoardCardProvider>
                </Dialog.Content>
            </Dialog.Root>
        </MemoryRouter>
    </QueryClientProvider>
);
