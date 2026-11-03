import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { queryClient } from "@/core/helpers/QueryClient";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import ProjectQuickSwitcher from "@/pages/DashboardPage/components/ProjectQuickSwitcher";
import ProjectExplorerSidebar from "@/pages/DashboardPage/components/ProjectExplorerSidebar";
import { PROJECT_QUICK_SWITCHER_EVENT } from "@/pages/DashboardPage/components/ProjectDiscovery";

const explorer = new URLSearchParams(location.search).has("explorer");
window.history.replaceState(null, "", "/board/fixture-project");
void useAuthStore
    .getState()
    .updateToken("fixture-session-1", api)
    .then(() => {
        createRoot(document.getElementById("root")!).render(
            <BrowserRouter>
                <QueryClientProvider client={queryClient}>
                    <AuthProvider>
                        <SocketProvider>
                            <button data-command-palette-trigger onClick={() => window.dispatchEvent(new Event(PROJECT_QUICK_SWITCHER_EVENT))}>
                                Open palette
                            </button>
                            {explorer && <ProjectExplorerSidebar />}
                            <ProjectQuickSwitcher />
                        </SocketProvider>
                    </AuthProvider>
                </QueryClientProvider>
            </BrowserRouter>
        );
    });
