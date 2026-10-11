import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser } from "@/core/models";
import AppPackagesPage from "./AppPackagesPage";
import "@/i18n";
import "@/assets/styles/main.css";
const currentUser = AuthUser.Model.fromOne({
    uid: "qa",
    created_at: new Date(),
    updated_at: new Date(),
    type: "user",
    email: "qa@example.test",
    firstname: "QA",
    lastname: "",
    username: "qa",
    is_admin: !new URLSearchParams(location.search).has("owner"),
    user_groups: [],
    subemails: [],
    setting_role_actions: ["*"],
    api_key_role_actions: [],
    mcp_role_actions: [],
});
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <AppPackagesPage currentUser={currentUser} />
    </QueryClientProvider>
);
