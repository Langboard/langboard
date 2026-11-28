import { createRoot } from "react-dom/client";
import { Suspense, lazy, useCallback, useRef, useState } from "react";
import { MemoryRouter } from "react-router";
import { createPlateEditor, Plate, useEditorSelector } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";
import { BasicBlocksKit } from "./plugins/basic-blocks-kit";
import { LinkKit } from "./plugins/link-kit";
import { SecretReferenceKit } from "./plugins/secret-reference-kit";
import { MarkdownKit } from "./plugins/markdown-kit";
import { Editor } from "@/components/plate-ui/editor";
import { FloatingToolbarButtons } from "@/components/plate-ui/floating-toolbar-buttons";
import { Toolbar } from "@/components/plate-ui/toolbar";
import { EditorDataProvider } from "@/core/providers/EditorDataProvider";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectWiki } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { convertWikiSelectionToSecret } from "@/controllers/api/wiki/useConvertWikiSelectionToSecret";
import { api } from "@/core/helpers/Api";
import { EEditorType } from "@langboard/core/constants";
import { mapWikiSecretSelection, wikiMarkdownRevision } from "./wiki-secret-selection";
import "@/i18n";
import "@/assets/styles/main.css";

api.defaults.baseURL = location.origin;
const actor = AuthUser.Model.fromOne({
    uid: "fixture-user",
    is_admin: true,
    firstname: "Fixture",
    lastname: "User",
    created_at: new Date(),
    updated_at: new Date(),
});
useAuthStore.setState({ currentUser: actor, state: "loaded" });
const source = "same 같은😀 same 같은😀 tail";
function SelectionState() {
    const selection = useEditorSelector((editor) => JSON.stringify(editor.selection), []);
    return <output data-testid="selection">{selection}</output>;
}
function Fixture() {
    const [editor] = useState(() =>
        createPlateEditor({
            plugins: [...BasicBlocksKit, ...LinkKit, ...SecretReferenceKit, ...MarkdownKit],
            value: (editor) => editor.getApi(MarkdownPlugin).markdown.deserialize(source),
        })
    );
    const epoch = useRef(0);
    const selectionChanged = useCallback(() => {
        epoch.current++;
    }, []);
    const [dirty, setDirty] = useState(false);
    const [pending, setPending] = useState(false);
    const [status, setStatus] = useState("");
    const [stored, setStored] = useState(source);
    const convert = async (name: string) => {
        const selection = mapWikiSecretSelection(editor, stored);
        if (!selection || dirty || pending) return;
        const version = epoch.current;
        const value = JSON.stringify(editor.children);
        const range = JSON.stringify(editor.selection);
        const isCurrent = () => epoch.current === version && JSON.stringify(editor.children) === value && JSON.stringify(editor.selection) === range;
        setPending(true);
        try {
            const content = await convertWikiSelectionToSecret(
                { project_uid: "fixture-project", wiki_uid: "fixture-wiki", expected_revision: await wikiMarkdownRevision(stored), name, selection },
                isCurrent
            );
            if (!isCurrent()) return;
            setStored(content.content.content);
            editor.tf.setValue(editor.getApi(MarkdownPlugin).markdown.deserialize(content.content.content));
            setStatus("Masked reference loaded");
        } catch {
            setStatus("Conversion unavailable; draft preserved");
        } finally {
            setPending(false);
        }
    };
    return (
        <MemoryRouter>
            <EditorDataProvider
                currentUser={actor}
                mentionables={[]}
                editorType={EEditorType.WikiContent}
                form={{ project_uid: "fixture-project", wiki_uid: "fixture-wiki", editor_generation: 0 }}
                wikiSecretSelection={{
                    selectionChanged,
                    disabled: dirty || pending,
                    isSupported: (editor) => !!mapWikiSecretSelection(editor, stored),
                    convert,
                }}
            >
                <main>
                    <Plate editor={editor} onValueChange={() => setDirty(true)}>
                        <Toolbar>
                            <FloatingToolbarButtons />
                        </Toolbar>
                        <Editor variant="none" />
                        <SelectionState />
                    </Plate>
                    <output data-testid="status">{status}</output>
                    <button onClick={() => useAuthStore.setState({ currentUser: null })}>End fixture session</button>
                </main>
            </EditorDataProvider>
        </MemoryRouter>
    );
}
const NativeWikiFixture = lazy(async () => {
    const [{ BoardWikiProvider }, { BoardWikiUnsavedProvider }, { default: WikiContent }] = await Promise.all([
        import("@/core/providers/BoardWikiProvider"),
        import("@/pages/BoardPage/components/wiki/BoardWikiUnsavedProvider"),
        import("@/pages/BoardPage/components/wiki/WikiContent"),
    ]);
    return {
        default: function NativeWikiFixture() {
            const [project] = useState(() =>
                Project.Model.fromOne({
                    uid: "fixture-project",
                    name: "Fixture project",
                    current_auth_role_actions: [],
                    created_at: new Date(),
                    updated_at: new Date(),
                })
            );
            const [wiki] = useState(() =>
                ProjectWiki.Model.fromOne({
                    uid: "fixture-wiki",
                    project_uid: "fixture-project",
                    title: "Native wiki fixture",
                    content: { content: source },
                    editor_generation: 0,
                    order: 0,
                    is_public: true,
                    forbidden: false,
                    assigned_members: [],
                    created_at: new Date(),
                    updated_at: new Date(),
                })
            );
            const [client] = useState(() => new QueryClient());
            return (
                <MemoryRouter initialEntries={["/board/fixture-project/wiki/fixture-wiki"]}>
                    <QueryClientProvider client={client}>
                        <BoardWikiProvider project={project} currentUser={actor} projectMembers={[]}>
                            <BoardWikiUnsavedProvider>
                                <WikiContent wiki={wiki} />
                            </BoardWikiUnsavedProvider>
                        </BoardWikiProvider>
                    </QueryClientProvider>
                </MemoryRouter>
            );
        },
    };
});
createRoot(document.getElementById("root")!).render(
    location.search.includes("native-wiki") ? (
        <Suspense fallback="Loading native wiki">
            <NativeWikiFixture />
        </Suspense>
    ) : (
        <Fixture />
    )
);
