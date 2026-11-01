import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser } from "@/core/models";
import WorkflowStagesPage from "./WorkflowStagesPage";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";
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
    is_admin: true,
    user_groups: [],
    subemails: [],
    setting_role_actions: new URLSearchParams(location.search).has("readonly") ? ["workflow_stage_read"] : ["*"],
    api_key_role_actions: [],
    mcp_role_actions: [],
});
useAuthStore.setState({ currentUser, state: "loaded", getToken: () => "workflow-local-fixture" });
window.addEventListener("workflow-fixture-logout", () => useAuthStore.getState().removeToken());
window.addEventListener("workflow-fixture-authenticate", () => void useAuthStore.getState().updateToken("workflow-local-next-session", api));
window.addEventListener("workflow-fixture-roles", (event) => {
    currentUser.setting_role_actions = (event as CustomEvent<AuthUser.TModel["setting_role_actions"]>).detail;
});
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <WorkflowStagesPage currentUser={currentUser} />
    </QueryClientProvider>
);
