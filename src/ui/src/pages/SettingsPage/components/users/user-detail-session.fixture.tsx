import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, User } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import MoreMenu from "@/components/MoreMenu";
import UserDetails from "@/pages/SettingsPage/components/users/UserDetails";
import i18n from "@/i18n";
import "@/core/injection";
import "@/assets/styles/main.css";

// Hold the production dialog mounted while native AuthStore.updateToken replaces its session.
const user = {
    uid: "fixture-admin",
    type: "user",
    firstname: "Fixture",
    lastname: "Admin",
    username: "fixture",
    is_admin: true,
    setting_role_actions: ["user_read"],
    api_key_role_actions: [],
    mcp_role_actions: [],
    created_at: new Date(),
    updated_at: new Date(),
};
useAuthStore.setState({ state: "loaded", pageLoaded: true, currentUser: AuthUser.Model.fromOne(user) });
const target = User.Model.fromOne({ ...user, uid: "00000000001" });
void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <MemoryRouter>
            <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
                <MoreMenu.Root>
                    <UserDetails user={target} />
                </MoreMenu.Root>
            </QueryClientProvider>
        </MemoryRouter>
    )
);
