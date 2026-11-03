// Local integration fixture. The production components own every save and processing request.
import "@/core/injection";
import { createRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { AuthUser, InternalBotModel, ProjectCard, ProjectCardAttachment } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import useAuthStore from "@/core/stores/AuthStore";
import { AppSettingProvider } from "@/core/providers/AppSettingProvider";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import BoardCardAttachmentMoreMenu from "@/pages/BoardPage/components/card/attachment/BoardCardAttachmentMoreMenu";
import InternalBotValue from "./InternalBotValue";
import Toast from "@/components/base/Toast";
import "@/i18n";
import "@/assets/styles/main.css";

const bootstrap = await fetch("/__native/bootstrap").then((response) => response.json());
api.defaults.baseURL = location.origin;
api.defaults.headers.common.Authorization = `Bearer ${bootstrap.access}`;
const actor = AuthUser.Model.fromOne(bootstrap.actor);
useAuthStore.setState({ currentUser: actor, state: "loaded" });
const bot = InternalBotModel.Model.fromOne(bootstrap.bot);
const card = ProjectCard.Model.fromOne(bootstrap.card);
const attachment = ProjectCardAttachment.Model.fromOne(bootstrap.attachment);
function Fixture() {
    const [isValidating, setIsValidating] = useState(false);
    return (
        <AppSettingProvider currentUser={actor}>
            <ModelRegistry.InternalBotModel.Provider model={bot}>
                <InternalBotValue />
            </ModelRegistry.InternalBotModel.Provider>
            <BoardCardProvider projectUID={bootstrap.project_uid} card={card} currentUser={actor} viewportRef={createRef()}>
                <BoardCardAttachmentMoreMenu attachment={attachment} isValidating={isValidating} setIsValidating={setIsValidating} />
            </BoardCardProvider>
            <Toast.Area />
        </AppSettingProvider>
    );
}
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
            <Fixture />
        </QueryClientProvider>
    </MemoryRouter>
);
