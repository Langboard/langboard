import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { AuthUser } from "@/core/models";
import { PlateEditor } from "./plate-editor";
import type { TEditor } from "./editor-kit";
import "@/assets/styles/main.css";
import "@/i18n";

const user = AuthUser.Model.fromOne({
    uid: "editor-ref-fixture",
    type: "user",
    firstname: "Editor",
    lastname: "Fixture",
    username: "editor-fixture",
    preferred_lang: "en-US",
    created_at: new Date(),
    updated_at: new Date(),
});

declare global {
    interface Window {
        editorFixtureSelection?: TEditor["selection"];
    }
}

function Fixture() {
    const editorRef = useRef<TEditor>(null);
    const [external, setExternal] = useState(false);
    const [content, setContent] = useState(new URLSearchParams(window.location.search).get("content") ?? "Original content");
    useEffect(() => {
        Object.defineProperty(window, "editorFixtureSelection", {
            configurable: true,
            get: () => editorRef.current?.selection,
        });
        return () => {
            delete window.editorFixtureSelection;
        };
    }, []);
    return (
        <main className="mx-auto max-w-xl p-6">
            <button className="btn" onClick={() => setExternal((current) => !current)}>
                Toggle external ref
            </button>
            <output data-testid="ref-mode">{external ? "external" : "internal"}</output>
            <PlateEditor
                editorType="view"
                currentUser={user}
                mentionables={[]}
                value={{ content }}
                setValue={(next) => setContent(next.content)}
                readOnly={false}
                editorRef={external ? editorRef : undefined}
                placeholder="Fixture editor"
            />
            <output data-testid="content">{content}</output>
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
