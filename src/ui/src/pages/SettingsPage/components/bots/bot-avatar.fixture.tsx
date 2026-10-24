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
    const currentUser = useAuthStore((state) => state.currentUser)!;
    return (
        <>
            <button onClick={() => setOpened(true)}>Reopen creation</button>
            <button
                onClick={() =>
                    useAuthStore.setState({
                        currentUser: AuthUser.Model.fromOne({ uid: "another-creator", created_at: new Date(), updated_at: new Date() }),
                    })
                }
            >
                Change actor
            </button>
            <button
                onClick={() =>
                    useAuthStore.setState({
                        currentUser: AuthUser.Model.fromOne({
                            uid: actor.uid,
                            created_at: new Date(),
                            updated_at: new Date(),
                            is_admin: false,
                            setting_role_actions: [],
                        }),
                    })
                }
            >
                Revoke authority
            </button>
            <BotCreateFormDialog opened={opened} setOpened={setOpened} currentUser={currentUser} />
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
