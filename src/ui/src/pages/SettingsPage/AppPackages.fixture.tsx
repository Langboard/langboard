import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser } from "@/core/models";
import AppPackagesPage from "./AppPackagesPage";
import "@/i18n";
import "@/assets/styles/main.css";
const makeUser = (uid: string) =>
    AuthUser.Model.fromOne({
        uid,
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
const client = new QueryClient();
function Fixture() {
    const [currentUser, setCurrentUser] = useState(() => makeUser("qa"));
    return (
        <QueryClientProvider client={client}>
            {new URLSearchParams(location.search).has("switch_user") && (
                <button onClick={() => setCurrentUser(makeUser("other-user"))}>Switch fixture user</button>
            )}
            <AppPackagesPage currentUser={currentUser} projectUid={new URLSearchParams(location.search).get("project_uid") ?? undefined} />
        </QueryClientProvider>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
