import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser } from "@/core/models";
import AppGroupsPage from "./AppGroupsPage";
import "@/i18n";
import "@/assets/styles/main.css";
const user = AuthUser.Model.fromOne({ uid: "qa", created_at: new Date(), updated_at: new Date(), type: "user", username: "qa", firstname: "QA", lastname: "", is_admin: !new URLSearchParams(location.search).has("owner") });
createRoot(document.getElementById("root")!).render(<QueryClientProvider client={new QueryClient()}><AppGroupsPage currentUser={user} /></QueryClientProvider>);
