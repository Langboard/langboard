import { ProjectCard } from "@/core/models";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import BoardCardDescriptionStaticChunk from "./BoardCardDescriptionStaticChunk";
import { AIChatEditor } from "@/components/plate-ui/ai-chat-editor";
import { AIChatPlugin, AIPlugin } from "@platejs/ai/react";
import { createPlateEditor, Plate } from "platejs/react";
import { ImagePlugin, openImagePreview } from "@platejs/media/react";
import { MediaPreviewDialog } from "@/components/plate-ui/media-preview-dialog";
import { API_URL } from "@/constants";
import { api } from "@/core/helpers/Api";
import "@/assets/styles/main.css";
import "@/i18n";

const chunk = {
    id: "internal-link-chunk",
    content: "Before [[card:fixture-card]] after https://example.com/fixture",
    metadata: {
        type: "paragraph",
        previewText: "Before [[card:fixture-card]] after",
        textLength: 34,
        isHeavy: false,
    },
} as const;

const card = new ProjectCard.Model({
    uid: "fixture-card",
    project_uid: "fixture-project",
    title: "Linked fixture card",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
});

function LocationProbe() {
    return <output data-location>{useLocation().pathname}</output>;
}

const root = createRoot(document.getElementById("root")!);
const previewMode = new URLSearchParams(window.location.search).has("preview");
const previewImage = { id: "preview-image", type: "img", url: `${API_URL}/file/fixture/card_attachment/preview.svg`, children: [{ text: "" }] };
const previewEditor = createPlateEditor({ plugins: [ImagePlugin], value: [previewImage] });
if (previewMode) {
    api.defaults.adapter = async (config) => ({
        config,
        status: 200,
        statusText: "OK",
        headers: {},
        data: new Blob(
            ["<svg xmlns='http://www.w3.org/2000/svg' width='800' height='500'><rect width='800' height='500' fill='currentColor'/></svg>"],
            { type: "image/svg+xml" }
        ),
    });
}

root.render(
    <MemoryRouter initialEntries={["/"]}>
        <main style={{ padding: 24, maxWidth: 700, margin: "auto" }}>
            {previewMode && (
                <div data-testid="transformed-card" style={{ transform: "translate(30px, 40px)", width: 300, height: 240, overflow: "hidden" }}>
                    <Plate editor={previewEditor}>
                        <button onClick={() => openImagePreview(previewEditor, previewImage)}>Open preview</button>
                        <MediaPreviewDialog />
                    </Plate>
                </div>
            )}
            <BoardCardDescriptionStaticChunk
                chunk={chunk}
                currentUser={undefined as never}
                mentionables={[]}
                cards={[card]}
                projectUID="fixture-project"
                cardUID="fixture-card-owner"
            />
            <LocationProbe />
            <Plate editor={createPlateEditor({ plugins: [AIPlugin, AIChatPlugin] })}>
                <AIChatEditor content="**Static AI preview**" />
            </Plate>
        </main>
    </MemoryRouter>
);
