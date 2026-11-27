import { useState } from "react";
import { useDragDropManager } from "react-dnd";
import SharedDndProvider from "@/components/SharedDndProvider";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/core/helpers/QueryClient";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import BoardRoutePage from "./RoutePage";
import WorkbenchRouteLayout from "@/components/Layout/WorkbenchRouteLayout";

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
function LocationReceipt() {
    const location = useLocation();
    return (
        <output hidden data-native-location>
            {location.pathname + location.search}
        </output>
    );
}
const runDndLifetime = new URLSearchParams(location.search).has("dnd-lifetime");
const managers = new WeakMap<ReturnType<typeof useDragDropManager>, number>();
let nextManager = 0;

function DndManagerProbe() {
    const manager = useDragDropManager();
    if (!managers.has(manager)) managers.set(manager, ++nextManager);
    return <output data-dnd-manager>{managers.get(manager)}</output>;
}

function DndLifetimeFixture() {
    const [visible, setVisible] = useState(true);
    return (
        <>
            <button type="button" onClick={() => setVisible((value) => !value)}>
                Toggle drag surfaces
            </button>
            {visible && (
                <>
                    <SharedDndProvider>
                        <DndManagerProbe />
                    </SharedDndProvider>
                    <SharedDndProvider>
                        <DndManagerProbe />
                    </SharedDndProvider>
                </>
            )}
        </>
    );
}

window.history.replaceState(null, "", "/board/left");
const board = (
    <BrowserRouter>
        <LocationReceipt />
        <QueryClientProvider client={queryClient}>
            <PageHeaderProvider>
                <AuthProvider>
                    <SocketProvider>
                        <Routes>
                            <Route element={<WorkbenchRouteLayout />}>
                                <Route path="/board/:projectUID/*" element={<BoardRoutePage />} />
                            </Route>
                        </Routes>
                    </SocketProvider>
                </AuthProvider>
            </PageHeaderProvider>
        </QueryClientProvider>
    </BrowserRouter>
);
createRoot(document.getElementById("root")!).render(runDndLifetime ? <DndLifetimeFixture /> : board);
