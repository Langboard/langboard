import { defineConfig, mergeConfig, transformWithEsbuild, type Plugin, type UserConfigFnObject } from "vite";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";
import base from "./vite.card-load.config";
import fixtureReadiness from "./vite.fixture-ready";

// Production provider, sections, navigation, section-save and HTTP controller.
// Only transport/editor widgets, picker and unrelated flip tray are substituted.
const boundaries: Record<string, string> = {
    "@/components/Collaborative/useCollaborativeText": `
        import { useCallback, useRef } from 'react';
        export function useCollaborativeText(props) {
            const latest = useRef(props); latest.current = props;
            const updateValue = useCallback(value => latest.current.onValueChange?.(value), []);
            const updateMeta = useCallback(() => {}, []);
            return { isSynced: !location.search.includes('unsynced'), updateValue, resetValue: updateValue, updateMeta };
        }`,
    "@/components/Collaborative": `
        import { useState, useEffect } from 'react';
        function Textarea(props) {
            const [value, setValue] = useState(props.defaultValue);
            useEffect(() => {
                props.onCollaborativeValueReady?.(setValue);
                props.onCollaborativeValueResetReady?.(setValue);
                return () => { props.onCollaborativeValueReady?.(null); props.onCollaborativeValueResetReady?.(null); };
            }, []);
            return <textarea aria-label="Title draft" ref={props.ref} value={value}
                onChange={event => { setValue(event.target.value); props.onValueChange(event.target.value); }} />;
        }
        export default { Textarea };`,
    "@/components/base/DateTimePicker": `
        export default function Picker({ value, disabled, onChange }) {
            return <input aria-label="Deadline draft" type="datetime-local" disabled={disabled}
                value={value ? value.toISOString().slice(0,16) : ''}
                onChange={event => onChange(event.target.value ? new Date(event.target.value + ':00Z') : undefined)} />;
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
    "./CardFlipTray": "export default function Tray() { return null; }",
};
export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        cacheDir: join(tmpdir(), "node_modules", "langboard-full-edit-cache"),
        optimizeDeps: { entries: ["src/pages/BoardPage/components/card/fullEditSave.fixture.html"] },
        define: { "process.env.API_URL": JSON.stringify("http://127.0.0.1:4297") },
        plugins: [
            fixtureReadiness("/src/pages/BoardPage/components/card/fullEditSave.fixture.tsx"),
            {
                name: "full-edit-fixture-boundaries",
                enforce: "pre",
                resolveId(id) {
                    const root = fileURLToPath(new URL("./src/", import.meta.url));
                    const source = id.startsWith(root) ? "@/" + id.slice(root.length) : id;
                    return source in boundaries ? `\0full-edit:${source}.jsx` : undefined;
                },
                async load(id) {
                    if (!id.startsWith("\0full-edit:")) return;
                    return transformWithEsbuild(boundaries[id.slice("\0full-edit:".length, -4)], id, { loader: "jsx", jsx: "automatic" });
                },
                transform(code, id) {
                    if (process.env.FULL_EDIT_BASELINE === "1" && id.endsWith("/BoardCard.tsx")) {
                        return code.replace(
                            /if \(getSectionDraftSnapshot\(\) === draftSnapshot\) \{\s*leaveCardEditMode\(session\);\s*\}/,
                            "leaveCardEditMode();"
                        );
                    }
                },
            } satisfies Plugin,
        ],
    })
);
