import { createRoot } from "react-dom/client";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import i18next from "i18next";
import { initReactI18next } from "react-i18next";
import useAuthStore from "@/core/stores/AuthStore";
import { API_URL } from "@/constants";
import VideoPlayer from ".";
import ImagePreviewDialog from "@/components/ImagePreviewDialog";
import { ProjectCardAttachment } from "@/core/models";
import { EditorDataProvider } from "@/core/providers/EditorDataProvider";
import { EEditorType } from "@langboard/core/constants";
import Markdown from "@/components/Markdown";
import BoardCardDescriptionStaticChunk from "@/pages/BoardPage/components/card/description/BoardCardDescriptionStaticChunk";

const surface = new URLSearchParams(location.search).get("surface");

await i18next.use(initReactI18next).init({ lng: "ko", resources: { ko: { translation: {} } } });
let session = 1;
useAuthStore.setState({ getToken: () => "fixture-session", getSessionVersion: () => session });

ProjectCardAttachment.Model.fromOne({
    uid: "fixture-attachment",
    card_uid: "fixture-card",
    name: "fixture.mp4",
    order: 0,
    url: `${API_URL}/file/qa/card_attachment/fixture.mp4`,
} as ProjectCardAttachment.Interface);

function Fixture() {
    const [visible, setVisible] = useState(true);
    const [src, setSrc] = useState(`${API_URL}/file/qa/card_attachment/fixture.mp4`);
    return (
        <main style={{ maxWidth: 720, margin: "24px auto" }}>
            <button onClick={() => setVisible(false)}>Unmount</button>
            <button
                onClick={() => {
                    session += 1;
                    useAuthStore.setState({ getToken: () => null, getSessionVersion: () => session });
                }}
            >
                Logout
            </button>
            <button onClick={() => setSrc("https://external.invalid/fixture.mp4")}>External</button>
            {visible &&
                (surface === "plate" ? (
                    <BoardCardDescriptionStaticChunk
                        chunk={{
                            id: "video",
                            content: `<video src='${src}' />`,
                            metadata: { type: "paragraph", previewText: "video", textLength: 5, isHeavy: true },
                        }}
                        currentUser={undefined as never}
                        mentionables={[]}
                        cards={[]}
                        projectUID="fixture-project"
                        cardUID="fixture-card"
                    />
                ) : surface === "preview" ? (
                    <ImagePreviewDialog
                        files={[
                            { name: "fixture.mp4", url: src },
                            { name: "image.svg", url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E", type: "image/svg+xml" },
                        ]}
                        initialIndex={0}
                        onClose={() => setVisible(false)}
                    />
                ) : surface === "markdown" ? (
                    <Markdown
                        message={{
                            content: `<video src="${src}" muted></video>`,
                        }}
                    />
                ) : (
                    <VideoPlayer src={src} muted />
                ))}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <EditorDataProvider
            currentUser={undefined as never}
            mentionables={[]}
            editorType={EEditorType.CardDescription}
            form={{ project_uid: "fixture-project", card_uid: "fixture-card" }}
        >
            <Fixture />
        </EditorDataProvider>
    </MemoryRouter>
);
