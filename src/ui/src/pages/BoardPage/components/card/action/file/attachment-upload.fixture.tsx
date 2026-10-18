import "@/core/injection";
import { api } from "@/core/helpers/Api";
import { createRef } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { AuthUser, ProjectCard } from "@/core/models";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import Toast from "@/components/base/Toast";
import BoardCardActionAttachFile from "./BoardCardActionAttachFile";
import "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
const card = new ProjectCard.Model({
    uid: "fixture-card",
    project_uid: "fixture",
    count_comment: 0,
    current_auth_role_actions: ["card_update"],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
});
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
            <BoardCardProvider projectUID="fixture" card={card} currentUser={{ uid: "fixture-user" } as AuthUser.TModel} viewportRef={createRef()}>
                <main className="p-8">
                    <BoardCardActionAttachFile buttonClassName="" />
                </main>
                <Toast.Area />
            </BoardCardProvider>
        </QueryClientProvider>
    </MemoryRouter>
);
