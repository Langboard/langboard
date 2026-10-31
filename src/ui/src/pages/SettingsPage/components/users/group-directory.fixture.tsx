import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser } from "@/core/models";
import { AppSettingProvider } from "@/core/providers/AppSettingProvider";
import { SettingRole } from "@/core/models/roles";
import SettingsPage from "@/pages/SettingsPage";
import { AuthProvider } from "@/core/providers/AuthProvider";
import UsersPage from "@/pages/SettingsPage/UsersPage";
import i18n from "@/i18n";
import useAuthStore from "@/core/stores/AuthStore";
import "@/core/injection";
import "@/assets/styles/main.css";

const mode = new URLSearchParams(location.search).get("mode") || "group";
const actions =
    mode === "manage"
        ? [SettingRole.EAction.GroupRead, SettingRole.EAction.GroupManage]
        : mode === "manageonly"
          ? [SettingRole.EAction.GroupManage]
          : mode === "group" || mode === "nonadmin"
            ? [SettingRole.EAction.GroupRead]
            : mode === "audit"
              ? [SettingRole.EAction.UserRead, SettingRole.EAction.UserAuditRead]
              : mode === "user"
                ? [SettingRole.EAction.UserRead]
                : mode === "create"
                  ? [SettingRole.EAction.UserCreate]
                  : mode === "both"
                    ? [SettingRole.EAction.UserRead, SettingRole.EAction.GroupRead]
                    : [];
const currentUser = AuthUser.Model.fromOne({
    uid: "fixture-admin",
    type: "user",
    firstname: "Fixture",
    lastname: "Admin",
    username: "fixture",
    is_admin: mode !== "nonadmin",
    setting_role_actions: actions,
    api_key_role_actions: [],
    mcp_role_actions: [],
    created_at: new Date(),
    updated_at: new Date(),
});
useAuthStore.setState({ state: "loaded", pageLoaded: true, currentUser });
function DirectoryFixture() {
    const activeUser = useAuthStore((state) => state.currentUser)!;
    return (
        <AppSettingProvider currentUser={activeUser}>
            <main className="p-6">
                <button
                    onClick={() => {
                        activeUser.setting_role_actions = [];
                    }}
                >
                    Revoke permissions
                </button>
                <button
                    onClick={() =>
                        useAuthStore.setState({
                            currentUser: AuthUser.Model.fromOne({
                                uid: "other-admin",
                                type: "user",
                                is_admin: true,
                                firstname: "Other",
                                lastname: "Admin",
                                username: "other",
                                setting_role_actions: [SettingRole.EAction.GroupRead],
                                api_key_role_actions: [],
                                mcp_role_actions: [],
                                created_at: new Date(),
                                updated_at: new Date(),
                            }),
                        })
                    }
                >
                    Switch account
                </button>
                <UsersPage />
            </main>
        </AppSettingProvider>
    );
}
void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <MemoryRouter initialEntries={["/settings/users"]}>
            <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
                <AuthProvider>{new URLSearchParams(location.search).has("shell") ? <SettingsPage /> : <DirectoryFixture />}</AuthProvider>
            </QueryClientProvider>
        </MemoryRouter>
    )
);
