import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import BoardRoutePage from "@/pages/BoardPage/RoutePage";
import BoardProxy from "@/pages/BoardPage";
import { BoardController } from "@/core/providers/BoardController";
import { BoardSplitProvider, BoardSplitPaneContext, useBoardSplitContext } from "@/core/providers/BoardSplitProvider";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { usePageNavigate } from "@/core/hooks/usePageNavigate";
import Button from "@/components/base/Button";

const splitFixture = new URLSearchParams(window.location.search).has("split");
function FixturePane() {
    const location = usePageLocation();
    const navigate = usePageNavigate();
    return (
        <>
            <Button onClick={() => navigate("/board/Source00001/Card0000001?filters=keyword:pane#pane")}>Load old card</Button>
            <output data-pane-location>{location.pathname + location.search + location.hash}</output>
            <BoardController projectUID={location.pathname.split("/")[2]}>
                <BoardProxy />
            </BoardController>
        </>
    );
}
function FixtureWorkspace() {
    const split = useBoardSplitContext()!;
    return (
        <>
            <Button onClick={() => split.open("Target00001")}>Open fixture pane</Button>
            {split.panes.map((pane) => (
                <section key={pane.id} data-fixture-pane={pane.id}>
                    <Button onClick={() => split.close(pane.id)}>Close fixture pane</Button>
                    <Button onClick={() => split.focus(pane.id)}>Focus fixture pane</Button>
                    <BoardSplitPaneContext.Provider value={pane.id}>
                        <PageNavigationScope
                            initialEntry={pane.initialEntry}
                            basePath="/board"
                            syncNative={split.focusedPane === pane.id}
                            onLocationChange={(location) => split.rememberLocation(pane.id, location.pathname + location.search + location.hash)}
                        >
                            <FixturePane />
                        </PageNavigationScope>
                    </BoardSplitPaneContext.Provider>
                </section>
            ))}
        </>
    );
}

const user = AuthUser.Model.fromOne({
    uid: "fixture-user",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    type: "user",
    firstname: "Fixture",
    lastname: "User",
    email: "fixture@example.com",
    username: "fixture",
    api_key_role_actions: ["*"],
    setting_role_actions: ["*"],
    mcp_role_actions: ["*"],
    user_groups: [],
    subemails: [],
    preferred_lang: "en",
    notification_unsubs: {},
    industry: "",
    purpose: "",
} as never);
useAuthStore.setState({ currentUser: user, state: "loaded" });
const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity } } });
function LocationReceipt() {
    const location = useLocation();
    return (
        <output hidden data-native-location>
            {location.pathname + location.search + location.hash}
        </output>
    );
}
window.history.replaceState(null, "", new URLSearchParams(window.location.search).get("entry") ?? "/board/Source00001/Card0000001");
createRoot(document.getElementById("root")!).render(
    <BrowserRouter>
        <LocationReceipt />
        <QueryClientProvider client={client}>
            <PageHeaderProvider>
                <AuthProvider>
                    <SocketProvider>
                        <Routes>
                            <Route
                                path="/board/:projectUID/*"
                                element={
                                    splitFixture ? (
                                        <BoardSplitProvider>
                                            <FixtureWorkspace />
                                        </BoardSplitProvider>
                                    ) : (
                                        <BoardRoutePage />
                                    )
                                }
                            />
                        </Routes>
                    </SocketProvider>
                </AuthProvider>
            </PageHeaderProvider>
        </QueryClientProvider>
    </BrowserRouter>
);
