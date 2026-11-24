import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { AuthUser, User } from "@/core/models";
import { PlateEditor } from "./plate-editor";
import type { TEditor } from "./editor-kit";
import "@/assets/styles/main.css";
import "@/i18n";

const base = { type: "user", firstname: "Mention", lastname: "Author", username: "author", created_at: new Date(), updated_at: new Date() };
const author = AuthUser.Model.fromOne({ ...base, uid: "mention-author", preferred_lang: "en-US" });
const people = [
    User.Model.fromOne({ ...base, uid: "mention-alice", firstname: "Alice", lastname: "Kim", username: "alice" }),
    User.Model.fromOne({ ...base, uid: "mention-bob", firstname: "Bob", lastname: "Lee", username: "bob" }),
    User.Model.fromOne({ ...base, uid: "mention-korean", firstname: "민지", lastname: "김", username: "minji" }),
];
function Fixture() {
    const editorRef = useRef<TEditor>(null);
    const [content, setContent] = useState("Draft before mention ");
    const [nodes, setNodes] = useState("");
    return (
        <main className="mx-auto max-w-2xl px-3" style={{ paddingTop: "min(45vh, 360px)" }}>
            <div data-testid="editor-boundary" className="rounded-md border bg-background">
                <PlateEditor
                    editorType="view"
                    currentUser={author}
                    mentionables={people}
                    value={{ content }}
                    setValue={(next) => {
                        setContent(next.content);
                        setNodes(JSON.stringify(editorRef.current?.children));
                    }}
                    readOnly={false}
                    editorRef={editorRef}
                    className="min-h-24 px-3 py-2"
                    placeholder="Mention fixture"
                />
            </div>
            <button onClick={() => setNodes(JSON.stringify(editorRef.current?.children))}>Inspect document</button>
            <output data-testid="serialized">{content}</output>
            <output data-testid="nodes" className="block break-all">
                {nodes}
            </output>
            <button data-testid="outside">Outside editor</button>
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
