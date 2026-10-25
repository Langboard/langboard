import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider, useIsMutating } from "@tanstack/react-query";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser, BotModel } from "@/core/models";
import Toast from "@/components/base/Toast";
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
    const [creationState, setCreationState] = useState("");
    const location = useLocation();
    const { toasts } = Toast.useToast();
    const pendingCreations = useIsMutating({ mutationKey: ["create-bot"] });
    const currentUser = useAuthStore((state) => state.currentUser)!;
    return (
        <>
            <button
                onClick={() =>
                    setCreationState(
                        JSON.stringify({ registered: !!BotModel.Model.getModel("late-created"), path: location.pathname, toasts: toasts.length })
                    )
                }
            >
                Inspect creation state
            </button>
            <output data-testid="creation-state" data-pending-creations={pendingCreations}>
                {creationState}
            </output>
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
            <button
                onClick={() => {
                    currentUser.is_admin = false;
                    currentUser.setting_role_actions = [];
                }}
            >
                Mutate authority
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
