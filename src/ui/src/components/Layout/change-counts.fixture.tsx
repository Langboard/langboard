import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
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
import useMarkCardSeen from "@/controllers/api/board/useMarkCardSeen";
import useCardReadState from "@/controllers/api/board/useCardReadState";
import BoardRoutePage from "@/pages/BoardPage/RoutePage";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import { ProjectCard } from "@/core/models";
import useUnreadChangeNavigation from "@/pages/BoardPage/components/card/useUnreadChangeNavigation";

function UnreadTarget() {
    const [target, setTarget] = useState("");
    useUnreadChangeNavigation((section, uid) => setTarget(`${section}:${uid ?? ""}`));
    return <output data-unread-target>{target}</output>;
}

function TargetReceipt() {
    const { projectUID, cardUID } = useParams();
    const currentUser = useAuthStore((state) => state.currentUser);
    const viewportRef = useRef<HTMLDivElement>(null);
    const card = ProjectCard.Model.getModel(cardUID!);
    return card && currentUser ? (
        <BoardCardProvider projectUID={projectUID!} card={card} currentUser={currentUser} viewportRef={viewportRef}>
            <UnreadTarget />
        </BoardCardProvider>
    ) : null;
}

function ReceiptControls() {
    const seen = useMarkCardSeen();
    const { readers } = useCardReadState("left", "left-card");
    const location = useLocation();
    return (
        <div>
            <button onClick={() => seen.mutate({ project_uid: "left", card_uid: "left-card" })}>Record seen receipt</button>
            <output data-seen-status>{seen.status}</output>
            <output data-reader-status>{readers.status}</output>
            <output hidden data-native-location>
                {location.pathname + location.search}
            </output>
        </div>
    );
}

window.history.replaceState(null, "", "/board/left");
void useAuthStore
    .getState()
    .updateToken("fixture-user-session", api)
    .then(() => {
        createRoot(document.getElementById("root")!).render(
            <BrowserRouter>
                <QueryClientProvider client={queryClient}>
                    <PageHeaderProvider>
                        <AuthProvider>
                            <SocketProvider>
                                <ReceiptControls />
                                <Routes>
                                    <Route path="/board/:projectUID/:cardUID" element={<TargetReceipt />} />
                                    <Route path="/board/:projectUID/*" element={<BoardRoutePage />} />
                                    <Route path="/empty" element={<p>Fixture route unmounted</p>} />
                                </Routes>
                            </SocketProvider>
                        </AuthProvider>
                    </PageHeaderProvider>
                </QueryClientProvider>
            </BrowserRouter>
        );
    });
