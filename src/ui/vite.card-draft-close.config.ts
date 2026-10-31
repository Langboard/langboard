import { defineConfig, mergeConfig, transformWithEsbuild, type Plugin, type UserConfigFnObject } from "vite";
import { fileURLToPath } from "node:url";
import base from "./vite.card-load.config";

// The affected page/card/provider/description/store stay native. Only the rich
// editor, collaboration transport and unrelated card panels are substituted.
const boundaries: Record<string, string> = {
    "@/components/Collaborative/useCollaborativeText": `
        import { useCallback, useRef } from 'react';
        export function useCollaborativeText(props) {
            const latest = useRef(props); latest.current = props;
            const updateValue = useCallback(value => latest.current.onValueChange?.(value), []);
            const updateMeta = useCallback(() => {}, []);
            return { isSynced: true, updateValue, resetValue: updateValue, updateMeta };
        }`,
    "@/components/Editor/plate-editor": `
        import { useEffect, useRef, useState } from 'react';
        export function PlateEditor(props) {
            const [value, setValue] = useState(props.value?.content ?? '');
            const content = useRef(value);
            const editor = useRef({ api: { markdown: { serialize: () => content.current } }, getTransforms: () => ({}), getApi: () => ({}) });
            useEffect(() => {
                props.editorRef.current = editor.current;
                props.onCollaborativeValueReady?.(() => {});
                props.onCollaborativeValueResetReady?.(value => { content.current = value; setValue(value); });
                return () => { props.editorRef.current = null; props.onCollaborativeValueReady?.(null); };
            }, []);
            return <textarea aria-label="Description draft" value={value} onChange={event => {
                content.current = event.target.value; setValue(event.target.value); props.onEditorChange?.(editor.current);
            }} />;
        }`,
};
for (const path of [
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
]) {
    boundaries[path] = "export default function UnrelatedPanel() { return null; }";
}

export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        // Keep optimized dependencies outside the React plugin's source transforms.
        cacheDir: "node_modules/.vite-draft-close",
        optimizeDeps: { entries: ["src/pages/BoardPage/components/card/cardDraftClose.fixture.html"] },
        server: {
            warmup: { clientFiles: ["./src/pages/BoardPage/components/card/cardDraftClose.fixture.tsx"] },
        },
        plugins: [
            {
                name: "card-draft-close-fixture-boundaries",
                enforce: "pre",
                configureServer(server) {
                    server.middlewares.use(async (request, _response, next) => {
                        const entry = "/src/pages/BoardPage/components/card/cardDraftClose.fixture.tsx";
                        if (request.url?.split("?")[0] !== entry) return next();
                        try {
                            await server.environments.client.transformRequest(entry);
                            await server.environments.client.waitForRequestsIdle();
                            next();
                        } catch (error) {
                            next(error);
                        }
                    });
                },
                resolveId(id, importer) {
                    const root = fileURLToPath(new URL("./src/", import.meta.url));
                    let source = id.startsWith(root) ? "@/" + id.slice(root.length) : id;
                    if (id.startsWith(".") && importer?.startsWith(root)) {
                        source = "@/" + fileURLToPath(new URL(id, `file://${importer}`)).slice(root.length);
                    }
                    return source in boundaries ? `\0draft-close:${source}.jsx` : undefined;
                },
                async load(id) {
                    if (!id.startsWith("\0draft-close:")) return;
                    return transformWithEsbuild(boundaries[id.slice("\0draft-close:".length, -4)], id, { loader: "jsx", jsx: "automatic" });
                },
            } satisfies Plugin,
        ],
    })
);
