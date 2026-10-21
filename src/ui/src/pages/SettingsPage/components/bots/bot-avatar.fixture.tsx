import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import BotCreateFormDialog from "./BotCreateFormDialog";
import "@/i18n";
import "@/assets/styles/main.css";
const actor = AuthUser.Model.fromOne({
    uid: "bot-creator",
    type: "user",
    username: "creator",
    firstname: "Bot",
    lastname: "Creator",
    is_admin: true,
    setting_role_actions: ["*"],
    preferred_lang: "en-US",
    created_at: new Date(),
    updated_at: new Date(),
});
useAuthStore.setState({ currentUser: actor, state: "loaded" });
function Fixture() {
    const [opened, setOpened] = useState(true);
    return <BotCreateFormDialog opened={opened} setOpened={setOpened} currentUser={actor} />;
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
