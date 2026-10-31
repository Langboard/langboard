import { defineConfig, mergeConfig, transformWithEsbuild, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import { fileURLToPath } from "node:url";

// Card lifecycle tests keep the native page, providers, sections and rich editor.
// Only unrelated panels are omitted; collaboration uses the local Yjs server.
const unrelatedPanels = new Set([
    "@/pages/BoardPage/components/card/CardFlipTray",
    "@/pages/BoardPage/components/card/decision/BoardCardDecisionGroup",
    "@/pages/BoardPage/components/card/action/BoardCardActionList",
    "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationship",
    "@/pages/BoardPage/components/card/BoardCardVisibility",
    "@/pages/BoardPage/components/card/BoardCardColumnName",
    "@/pages/BoardPage/components/card/BoardCardMemberList",
    "@/pages/BoardPage/components/card/label/BoardCardLabelList",
    "@/pages/BoardPage/components/card/CardColumnHistory",
    "@/pages/BoardPage/components/card/CardTimestamps",
    "@/pages/BoardPage/components/card/CardAppArchiveReceipts",
    "@/pages/BoardPage/components/card/comment/BoardCommentForm",
    "@/pages/BoardPage/components/card/comment/BoardCommentList",
    "@/pages/BoardPage/components/task/BoardTaskMetadataSection",
    "@/components/CardTypeBadges",
]);

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        plugins: [
            {
                name: "collaborative-card-draft-unrelated-panels",
                enforce: "pre",
                resolveId(id: string, importer?: string) {
                    const root = fileURLToPath(new URL("./src/", import.meta.url));
                    let source = id.startsWith(root) ? "@/" + id.slice(root.length) : id;
                    if (id.startsWith(".") && importer?.startsWith(root)) {
                        source = "@/" + fileURLToPath(new URL(id, `file://${importer}`)).slice(root.length);
                    }
                    const nativeComments =
                        env.mode === "responsive-comments" &&
                        (source === "@/pages/BoardPage/components/card/comment/BoardCommentForm" ||
                            source === "@/pages/BoardPage/components/card/comment/BoardCommentList");
                    return unrelatedPanels.has(source) && !nativeComments ? `\0collaborative-card-draft:${source}.jsx` : undefined;
                },
                async load(id: string) {
                    if (!id.startsWith("\0collaborative-card-draft:")) return;
                    return transformWithEsbuild("export default function UnrelatedPanel() { return null; }", id, {
                        loader: "jsx",
                        jsx: "automatic",
                    });
                },
            },
        ],
        resolve: {
            alias: [
                {
                    find: "@/core/providers/SocketProvider",
                    replacement: fileURLToPath(new URL("./src/components/Editor/collaborative-socket.fixture.ts", import.meta.url)),
                },
            ],
        },
    })
);
