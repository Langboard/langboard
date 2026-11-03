import { useRef, useState } from "react";
import { getAdapter } from "axios";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { AuthUser } from "@/core/models";
import { EEditorType } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { PlateEditor } from "./plate-editor";
import "@/assets/styles/main.css";
import "@/i18n";

const user = AuthUser.Model.fromOne({
    uid: "draft-fixture",
    type: "user",
    firstname: "Draft",
    lastname: "Fixture",
    username: "draft-fixture",
    preferred_lang: "en-US",
    created_at: new Date(),
    updated_at: new Date(),
});
api.defaults.adapter = async (config) => {
    if (config.url === "/editor-sync/rich/patch-request") {
        // Test identity only; transport and arbitration use the real Phoenix owner.
        config.headers.set("X-Api-Token", "fixture-only");
        return getAdapter("xhr")({ ...config, baseURL: "http://127.0.0.1:4243" });
    }
    return { status: 200, statusText: "OK", headers: {}, config, data: { requests: [] } };
};

function Fixture() {
    const [serverContent, setServerContent] = useState("Initial server content");
    const [ready, setReady] = useState(false);
    const reset = useRef<((value: string) => void) | null>(null);
    return (
        <main className="mx-auto max-w-xl p-6">
            <button className="btn" onClick={() => setServerContent("Delayed server content")}>
                Delayed server update
            </button>
            <button className="btn" onClick={() => reset.current?.("Explicit restored content")}>
                Explicit reset
            </button>
            <output data-testid="ready">{ready ? "ready" : "waiting"}</output>
            <PlateEditor
                currentUser={user}
                mentionables={[]}
                editorType={EEditorType.CardDescription}
                form={{ project_uid: "fixture", card_uid: new URLSearchParams(location.search).get("card") ?? "draft" }}
                value={{ content: serverContent }}
                setValue={() => {}}
                serializeOnChange={false}
                authoritativeCollaborativeValue={serverContent}
                onCollaborativeValueReady={(update) => setReady(!!update)}
                onCollaborativeValueResetReady={(callback) => {
                    reset.current = callback;
                }}
            />
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <DndProvider backend={HTML5Backend}>
                <Fixture />
            </DndProvider>
        </MemoryRouter>
    </QueryClientProvider>
);
