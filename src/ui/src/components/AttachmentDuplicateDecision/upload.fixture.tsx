import "@/core/injection";
import { createRef, StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createPlateEditor, Plate } from "platejs/react";
import { FilePlugin, PlaceholderPlugin } from "@platejs/media/react";
import { KEYS } from "platejs";
import { EEditorType } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard, ProjectCardAttachment } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import { EditorDataProvider } from "@/core/providers/EditorDataProvider";
import BoardCardActionAttachFile from "@/pages/BoardPage/components/card/action/file/BoardCardActionAttachFile";
import { PlaceholderElement } from "@/components/plate-ui/media-placeholder-node";
import { FileElement } from "@/components/plate-ui/media-file-node";
import { Editor } from "@/components/plate-ui/editor";
import Toast from "@/components/base/Toast";
import "@/i18n";
import "@/assets/styles/main.css";

api.defaults.baseURL = location.origin;
const user = new AuthUser.Model({ uid: "actor", firstname: "Upload", lastname: "Fixture" });
useAuthStore.setState({ currentUser: user, state: "loaded" });
function Media({ cardUID, onUploaded }: { cardUID: string; onUploaded: () => void }) {
    const editor = useMemo(
        () =>
            createPlateEditor({
                plugins: [FilePlugin.withComponent(FileElement), PlaceholderPlugin.configure({ render: { node: PlaceholderElement } })],
                value: [
                    { id: "source-paragraph", type: "p", children: [{ text: "Preserve this source content" }] },
                    { id: "upload-placeholder", type: KEYS.placeholder, mediaType: KEYS.file, children: [{ text: "" }] },
                ],
            }),
        [cardUID]
    );
    (window as unknown as { readUploadEditor: () => unknown }).readUploadEditor = () => editor.children;
    return (
        <EditorDataProvider
            currentUser={user}
            mentionables={[]}
            editorType={EEditorType.CardDescription}
            form={{ project_uid: "fixture", card_uid: cardUID }}
            uploadedCallback={(data) => {
                ProjectCardAttachment.Model.fromOne(data, true);
                onUploaded();
            }}
        >
            <Plate editor={editor}>
                <Editor variant="none" />
            </Plate>
        </EditorDataProvider>
    );
}
function Fixture() {
    const [cardUID, setCardUID] = useState("fixture-card");
    const [mounted, setMounted] = useState(true);
    const [callbacks, setCallbacks] = useState(0);
    const card = useMemo(
        () =>
            ProjectCard.Model.fromOne({
                uid: cardUID,
                project_uid: "fixture",
                title: "Upload fixture",
                count_comment: 0,
                current_auth_role_actions: ["card_update"],
                created_at: new Date(),
                updated_at: new Date(),
            }),
        [cardUID]
    );
    return (
        <main className="space-y-4 p-8">
            <button className="btn" onClick={() => setCardUID("other-card")}>
                Change source
            </button>
            <button className="btn" onClick={() => useAuthStore.getState().removeToken()}>
                Change account
            </button>
            <button className="btn" onClick={() => setMounted(false)}>
                Unmount upload
            </button>
            <output data-testid="callbacks">{callbacks}</output>
            <button
                className="btn"
                onClick={() => {
                    const names = ProjectCardAttachment.Model.getModels(() => true).map((model) => model.name);
                    document.querySelector("[data-testid=models]")!.textContent = JSON.stringify(names);
                }}
            >
                Read models
            </button>
            <output data-testid="models" />
            {mounted && (
                <BoardCardProvider projectUID="fixture" card={card} currentUser={user} viewportRef={createRef()}>
                    <BoardCardActionAttachFile buttonClassName="" />
                    <section aria-label="Editor media">
                        <Media cardUID={cardUID} onUploaded={() => setCallbacks((count) => count + 1)} />
                    </section>
                </BoardCardProvider>
            )}
            <Toast.Area />
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <MemoryRouter>
            <QueryClientProvider client={new QueryClient()}>
                <Fixture />
            </QueryClientProvider>
        </MemoryRouter>
    </StrictMode>
);
