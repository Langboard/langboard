import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router";
import { QueryClient, QueryClientProvider, useIsMutating } from "@tanstack/react-query";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser, BotModel } from "@/core/models";
import Toast from "@/components/base/Toast";
import BotCreateFormDialog from "./BotCreateFormDialog";
import BotsPage from "@/pages/SettingsPage/BotsPage";
import { AppSettingProvider } from "@/core/providers/AppSettingProvider";
import "@/core/injection";
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
function EditFixture() {
    const location = useLocation();
    const currentUser = useAuthStore((state) => state.currentUser)!;
    const pendingUpdates = useIsMutating({ mutationKey: ["update-bot"] });
    const [modelState, setModelState] = useState("");
    return (
        <AppSettingProvider currentUser={currentUser}>
            <nav aria-label="Fixture bot navigation">
                <Link to="/settings/bots/avatar-a">Select bot A</Link>
                <Link to="/settings/bots/avatar-b">Select bot B</Link>
                <Link to="/settings/bots">Bot list</Link>
            </nav>
            <output data-testid="edit-location" data-pending-updates={pendingUpdates}>
                {location.pathname}
            </output>
            <button
                className="btn"
                onClick={() =>
                    setModelState(
                        JSON.stringify(
                            ["avatar-a", "avatar-b"].map((uid) => {
                                const bot = BotModel.Model.getModel(uid);
                                return { uid, avatar: bot?.avatar, name: bot?.name };
                            })
                        )
                    )
                }
            >
                Inspect bot models
            </button>
            <output data-testid="edit-models">{modelState}</output>
            <button
                className="btn"
                onClick={() => {
                    currentUser.setting_role_actions = [];
                }}
            >
                Revoke edit authority
            </button>
            <button
                className="btn"
                onClick={() => {
                    currentUser.setting_role_actions = ["*"];
                }}
            >
                Restore edit authority
            </button>
            <Routes>
                <Route path="/settings/bots/:botUID?" element={<BotsPage />} />
            </Routes>
        </AppSettingProvider>
    );
}
const editing = new URLSearchParams(window.location.search).get("mode") === "edit";
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={editing ? ["/settings/bots/avatar-a"] : ["/"]}>{editing ? <EditFixture /> : <Fixture />}</MemoryRouter>
    </QueryClientProvider>
);
