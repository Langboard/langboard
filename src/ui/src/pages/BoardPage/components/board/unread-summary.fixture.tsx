import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { queryClient } from "@/core/helpers/QueryClient";
import { AuthUser, ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import useSocketStore from "@/core/stores/SocketStore";
import { ESocketTopic } from "@langboard/core/enums";
import { installNativeSocketFixture } from "./wiki-summary-tests/nativeSocket.fixture";
import BoardChangesSidebar from "./BoardChangesSidebar";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";

function selectActor(uid: string) {
    useAuthStore.setState({
        state: "loaded",
        pageLoaded: true,
        currentUser: AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() }),
    });
}
selectActor("actor-a");
installNativeSocketFixture();
const socket = useSocketOutsideProvider();
socket.subscribe(ESocketTopic.Board, ["left", "right"]);
socket.subscribe(ESocketTopic.UserPrivate, ["actor-a", "actor-b"]);
Object.assign(window, {
    unreadFixture: {
        selectActor,
        models: () => ProjectCard.Model.getModels(() => true).length,
        invalidateBoard: () => queryClient.invalidateQueries({ queryKey: ["get-cards-left"] }),
        invalidateReadState: () => queryClient.invalidateQueries({ queryKey: ["card-read-state", "left", "card"] }),
        reconnect: () => {
            const callbacks = useSocketStore.getState().getStore().defaultEvents.open ?? {};
            Object.values(callbacks)
                .flat()
                .forEach((callback) => callback(new Event("open")));
        },
    },
});
function Fixture() {
    const [board, setBoard] = useState("left");
    const [visible, setVisible] = useState(true);
    const location = useLocation();
    // Active board cache exists independently; opening Changes must not add an observer/request.
    useQuery({ queryKey: ["get-cards-left"], queryFn: async () => ({ isUpdated: true }), initialData: { isUpdated: true }, staleTime: Infinity });
    return (
        <>
            <button onClick={() => setBoard((value) => (value === "left" ? "right" : "left"))}>Switch board</button>
            <button onClick={() => setVisible((value) => !value)}>Toggle Changes</button>
            <output data-location>{location.pathname}</output>
            {visible && <BoardChangesSidebar projectUID={board} />}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <QueryClientProvider client={queryClient}>
            <Fixture />
        </QueryClientProvider>
    </MemoryRouter>
);
