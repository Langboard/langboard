import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser, InternalBotModel, Project } from "@/core/models";
import { AccountSettingProvider } from "@/core/providers/AccountSettingProvider";
import ProfilePage from "@/pages/AccountPage/ProfilePage";
import BoardSettingsBasic from "@/pages/BoardPage/components/settings/BoardSettingsBasic";
import { BoardSettingsProvider } from "@/core/providers/BoardSettingsProvider";
import "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const baseUser = {
    uid: "avatar-user",
    type: "user",
    username: "avatar-user",
    firstname: "Avatar",
    lastname: "User",
    is_admin: params.has("admin"),
    setting_role_actions: params.get("roles") === "granted" ? ["*"] : undefined,
    preferred_lang: "en-US",
    created_at: new Date(),
    updated_at: new Date(),
    avatar: undefined,
};
const user = AuthUser.Model.fromOne(baseUser);
useAuthStore.setState({ currentUser: user, state: "loaded" });
const project = Project.Model.fromOne({
    uid: "avatar-board",
    title: "Board",
    owner_uid: user.uid,
    created_at: new Date(),
    updated_at: new Date(),
    project_type: "SW",
    current_auth_role_actions: ["*"],
    description: "Board description",
    archive_visible_days: 3,
});
const summary = InternalBotModel.Model.fromOne({
    uid: "summary",
    bot_type: "avatar_summary",
    display_name: "Summary model",
    platform: "default",
    platform_running_type: "default",
    value: JSON.stringify({ model_name: "text-model" }),
    created_at: new Date(),
    updated_at: new Date(),
});
function Fixture() {
    const actor = useAuthStore((state) => state.currentUser)!;
    const [allowed, setAllowed] = useState(true);
    const [board, setBoard] = useState(false);
    const avatar = project.useField("avatar");
    return (
        <main className="mx-auto max-w-3xl p-3">
            <nav className="flex flex-wrap gap-3">
                <button
                    onClick={() => {
                        summary.value = JSON.stringify({ model_name: "changed-model" });
                    }}
                >
                    Change summary profile
                </button>
                <button
                    onClick={() => {
                        actor.avatar_revision = 9;
                    }}
                >
                    Advance avatar revision
                </button>
                <button onClick={() => setBoard(!board)}>Toggle target</button>
                <button
                    onClick={() => {
                        setAllowed(!allowed);
                        project.current_auth_role_actions = allowed ? [] : ["*"];
                    }}
                >
                    Toggle permission
                </button>
                <button onClick={() => useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...baseUser, uid: "other-user" }) })}>
                    Switch actor
                </button>
            </nav>
            {board ? (
                <BoardSettingsProvider project={project} currentUser={actor}>
                    <BoardSettingsBasic />
                    <output data-testid="board-avatar">{avatar ?? "unchanged"}</output>
                </BoardSettingsProvider>
            ) : (
                <AccountSettingProvider currentUser={actor}>
                    <ProfilePage />
                </AccountSettingProvider>
            )}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
