import { createRoot } from "react-dom/client";
import { useRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, ProjectCard } from "@/core/models";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import { api } from "@/core/helpers/Api";
import BoardCardVisibility from "./BoardCardVisibility";
import "@/i18n";
import "@/assets/styles/main.css";

const card = ProjectCard.Model.fromOne({
    uid: "visibility-fixture",
    project_uid: "fixture",
    title: "Visibility",
    visibility: "INTERNAL",
    can_change_visibility: true,
    current_auth_role_actions: ["*"],
    created_at: new Date(),
    updated_at: new Date(),
});
const user = AuthUser.Model.fromOne({ uid: "fixture-user", firstname: "Fixture", created_at: new Date(), updated_at: new Date() });
let fail = false;
api.defaults.adapter = async (config) => {
    if (fail) throw new Error("Fixture denied");
    const data = JSON.parse(config.data);
    document.querySelector("#request")!.textContent = JSON.stringify(data);
    return { data: { visibility: data.visibility, updated_at: new Date().toISOString() }, status: 200, statusText: "OK", headers: {}, config };
};
function Fixture() {
    const ref = useRef<HTMLDivElement>(null);
    const visibility = card.useField("visibility");
    return (
        <QueryClientProvider client={new QueryClient()}>
            <main className="p-4" ref={ref}>
                <h1>Card visibility editor</h1>
                <BoardCardProvider projectUID="fixture" card={card} currentUser={user} viewportRef={ref}>
                    <BoardCardVisibility />
                </BoardCardProvider>
                <output id="state">{visibility}</output>
                <output id="request" className="block break-all text-xs" />
                <button
                    onClick={() => {
                        fail = true;
                    }}
                >
                    Fail requests
                </button>
                <button
                    onClick={() => {
                        card.visibility = "PRIVATE";
                    }}
                >
                    Private card
                </button>
                <button
                    onClick={() => {
                        card.visibility = "SHARED";
                        ProjectCard.Model.fromOne({ uid: card.uid, can_change_visibility: false } as ProjectCard.Interface);
                    }}
                >
                    Remove permission
                </button>
            </main>
        </QueryClientProvider>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
