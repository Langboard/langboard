import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { createPlateEditor, Plate } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";
import { YjsPlugin } from "@platejs/yjs/react";
import { EEditorType } from "@langboard/core/constants";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { BasicBlocksKit } from "../basic-blocks-kit";
import { ListKit } from "../list-kit";
import { MarkdownKit } from "../markdown-kit";
import { BasicMarksKit } from "../basic-marks-kit";
import { Editor } from "@/components/plate-ui/editor";
import messages from "@/assets/locales/en-US/editor.json";
import { DescriptionMarkdownEditor } from "./DescriptionMarkdownEditor";
import { MarkdownSourcePlugin } from "./markdown-source-plugin";
import "@/assets/styles/main.css";

void i18n.use(initReactI18next).init({ lng: "en", resources: { en: { translation: { editor: messages } } }, interpolation: { escapeValue: false } });
const initial =
    "## Original\n\nA **bold** paragraph.\n\n" + Array.from({ length: 45 }, (_, index) => `Paragraph ${index}: long source text.\n`).join("\n");

function Fixture() {
    const [user, setUser] = useState("user-a");
    const [comment, setComment] = useState(false);
    const [saved, setSaved] = useState("");
    const [ready, setReady] = useState(false);
    const [editor] = useState(() =>
        createPlateEditor({
            plugins: [
                ...BasicBlocksKit,
                ...BasicMarksKit,
                ...ListKit,
                ...MarkdownKit,
                MarkdownSourcePlugin,
                YjsPlugin.configure({ options: { providers: [] } }),
            ],
            value: (current) => current.getApi(MarkdownPlugin).markdown.deserialize(initial),
        })
    );
    useEffect(() => {
        const api = editor.getApi(YjsPlugin).yjs;
        const { ydoc: document, awareness } = editor.getOptions(YjsPlugin);
        let disposed = false;
        editor.setOptions(YjsPlugin, {
            providers: [{ type: "fixture", document, awareness, connect() {}, disconnect() {}, destroy() {}, isConnected: true, isSynced: true }],
        });
        void Promise.resolve(api.init({ autoConnect: false, value: editor.getApi(MarkdownPlugin).markdown.deserialize(initial) })).then(() => {
            if (!disposed) setReady(true);
        });
        return () => {
            disposed = true;
            api.destroy();
            awareness?.destroy();
            document.destroy();
        };
    }, [editor]);
    return (
        <main className="mx-auto max-w-6xl p-4">
            <label>
                User
                <select aria-label="User" value={user} onChange={(event) => setUser(event.target.value)}>
                    <option value="user-a">User A</option>
                    <option value="user-b">User B</option>
                </select>
            </label>
            <label>
                <input type="checkbox" checked={comment} onChange={(event) => setComment(event.target.checked)} />
                Comment editor
            </label>
            <button type="button" onClick={() => setSaved(editor.getApi(MarkdownPlugin).markdown.serialize())}>
                Save draft
            </button>
            <button type="button" onClick={() => editor.tf.insertText("Remote ", { at: { path: [0, 0], offset: 0 } })}>
                Remote edit
            </button>
            <button
                type="button"
                onClick={() =>
                    editor.tf.apply({
                        type: "set_node",
                        path: [0],
                        properties: { id: editor.children[0].id },
                        newProperties: { id: crypto.randomUUID() },
                    })
                }
            >
                Normalize document identity
            </button>
            <Plate editor={editor}>
                <DescriptionMarkdownEditor
                    key={user}
                    editor={editor}
                    userUID={user}
                    cardUID="fixture-card"
                    editorType={comment ? EEditorType.CardComment : EEditorType.CardDescription}
                    initialSource={initial}
                    disabled={!ready}
                    onSourceChange={() => undefined}
                >
                    {(split) => <Editor variant="ai" readOnly={split} aria-label="Rich content" />}
                </DescriptionMarkdownEditor>
            </Plate>
            <pre data-saved-draft>{saved}</pre>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
