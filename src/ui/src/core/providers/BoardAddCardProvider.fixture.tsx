import "@/core/injection";
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser, Project, ProjectColumn } from "@/core/models";
import { BoardProvider } from "@/core/providers/BoardProvider";
import { BoardAddCardProvider, useBoardAddCard } from "./BoardAddCardProvider";
import BoardColumnAddCard from "@/pages/BoardPage/components/board/BoardColumnAddCard";
import BoardColumnAddCardButton from "@/pages/BoardPage/components/board/BoardColumnAddCardButton";
import useAuthStore from "@/core/stores/AuthStore";
import "@/i18n";
import "@/assets/styles/main.css";

const base = { created_at: new Date(), updated_at: new Date() };
const users = ["actor-a", "actor-b"].map((uid) => AuthUser.Model.fromOne({ ...base, uid, is_admin: true, firstname: uid }));
const projects = ["project-a", "project-b"].map((uid) =>
    Project.Model.fromOne({
        ...base,
        uid,
        title: uid,
        all_members: users,
        labels: [],
        invited_member_uids: [],
        current_auth_role_actions: ["*"],
    })
);
const columns = ["column-a", "column-b"].map((uid) =>
    ProjectColumn.Model.fromOne({
        ...base,
        uid,
        project_uid: projects[0].uid,
        name: uid,
        is_archive: false,
        order: 0,
        count: 0,
    })
);
useAuthStore.setState({ currentUser: users[0], state: "loaded" });
const requests: Array<{ payload: unknown; release: (success: boolean) => void }> = [];
api.defaults.adapter = async (config) => {
    if (config.method !== "post") return { data: {}, status: 200, statusText: "OK", headers: {}, config };
    const success = await new Promise<boolean>((resolve) => {
        requests.push({ payload: typeof config.data === "string" ? JSON.parse(config.data) : config.data, release: resolve });
    });
    if (!success)
        throw new AxiosError("Controlled create failure", "ERR_BAD_RESPONSE", config, undefined, {
            data: { message: "Controlled create failure" },
            status: 500,
            statusText: "Error",
            headers: {},
            config,
        });
    return { data: { card: { uid: "created-fixture" } }, status: 200, statusText: "OK", headers: {}, config };
};
Object.assign(window, {
    cardCreationFixture: {
        requests,
        release: (index: number, success: boolean) => requests[index].release(success),
    },
});

function ComposerControls() {
    const { changeMode, setIsEditing, saveTitleDraft, saveDescriptionDraft } = useBoardAddCard();
    // These controls exercise session/revision races through the real provider.
    Object.assign(window, { composerFixture: { saveTitleDraft, saveDescriptionDraft, changeMode, setIsEditing } });
    return (
        <>
            <BoardColumnAddCard />
            <BoardColumnAddCardButton />
            <button data-disable-change-mode onClick={() => changeMode("edit")}>
                Reopen session
            </button>
        </>
    );
}
function Fixture() {
    const [actor, setActor] = useState(0);
    const [project, setProject] = useState(0);
    const [column, setColumn] = useState(0);
    const [visible, setVisible] = useState(true);
    const viewportRef = useRef<HTMLDivElement>(null);
    return (
        <main className="w-full max-w-sm p-4">
            <nav data-disable-change-mode>
                <button
                    onClick={() => {
                        useAuthStore.setState({ currentUser: users[1 - actor] });
                        setActor(1 - actor);
                    }}
                >
                    Switch account
                </button>
                <button
                    onClick={() => {
                        useAuthStore.getState().removeToken();
                        useAuthStore.setState({ currentUser: users[actor], state: "loaded" });
                    }}
                >
                    Reauthenticate
                </button>
                <button onClick={() => setProject(1 - project)}>Switch project</button>
                <button onClick={() => setColumn(1 - column)}>Switch column</button>
                <button onClick={() => setVisible(!visible)}>Toggle composer</button>
            </nav>
            <output data-testid="scope">
                {users[actor].uid}:{projects[project].uid}:{columns[column].uid}
            </output>
            <BoardProvider project={projects[project]} currentUser={users[actor]}>
                {visible && (
                    <BoardAddCardProvider column={columns[column]} viewportRef={viewportRef} toLastPage={() => {}} isDefaultCardColumn>
                        <div ref={viewportRef}>
                            <ComposerControls />
                        </div>
                    </BoardAddCardProvider>
                )}
            </BoardProvider>
        </main>
    );
}
const router = createBrowserRouter([{ path: "*", element: <Fixture /> }]);
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
