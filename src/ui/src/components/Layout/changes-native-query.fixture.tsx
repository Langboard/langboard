import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation, useParams } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import { queryClient } from "@/core/helpers/QueryClient";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import BoardRoutePage from "@/pages/BoardPage/RoutePage";
import BoardChangesSidebar from "@/pages/BoardPage/components/board/BoardChangesSidebar";

function LocationReceipt() {
    const location = useLocation();
    return (
        <output hidden data-native-location>
            {location.pathname + location.search}
        </output>
    );
}

function StandaloneChanges() {
    const { projectUID } = useParams();
    return <BoardChangesSidebar projectUID={projectUID!} />;
}

const initial = new URLSearchParams(location.search).get("initial") ?? "/board/left";
window.history.replaceState(null, "", initial);
void useAuthStore
    .getState()
    .updateToken("fixture-user-session", api)
    .then(() => {
        createRoot(document.getElementById("root")!).render(
            <BrowserRouter>
                <LocationReceipt />
                <QueryClientProvider client={queryClient}>
                    <PageHeaderProvider>
                        <AuthProvider>
                            <SocketProvider>
                                <Routes>
                                    <Route path="/board/:projectUID/*" element={<BoardRoutePage />} />
                                    <Route path="/changes/:projectUID" element={<StandaloneChanges />} />
                                    <Route path="/empty" element={<p>Fixture route unmounted</p>} />
                                </Routes>
                            </SocketProvider>
                        </AuthProvider>
                    </PageHeaderProvider>
                </QueryClientProvider>
            </BrowserRouter>
        );
    });
