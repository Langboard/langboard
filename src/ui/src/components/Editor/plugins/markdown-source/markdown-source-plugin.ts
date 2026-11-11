import { createPlatePlugin } from "platejs/react";
import { ElementApi, TextApi, type Descendant, type SlateEditor, type Value } from "platejs";
import { MarkdownPlugin, type SerializeMdOptions } from "@platejs/markdown";

export const MarkdownSourcePlugin = createPlatePlugin({
    key: "markdownSource",
    options: { source: null as string | null, snapshot: "" },
}).extendApi(({ editor, setOption }) => ({
    remember: (source: string) => {
        setOption("source", source);
        setOption("snapshot", JSON.stringify(editor.children));
    },
}));

export function preservedSource(editor: SlateEditor, options?: SerializeMdOptions): string | undefined {
    const state = editor.getOptions(MarkdownSourcePlugin);
    if (!state) return;
    const { source, snapshot } = state;
    if (!Object.keys(options ?? {}).length && source !== null && snapshot === JSON.stringify(editor.children)) return source;
}

export function matchingInitialSource(editor: SlateEditor, source: string | undefined): string | undefined {
    if (source === undefined) return;
    const markdown = editor.getApi(MarkdownPlugin).markdown;
    try {
        // Compare rendered meaning through the existing serializer, allowing
        // normalization to append its empty trailing paragraph. Never trim the
        // source returned to the caller or replace a different live document.
        const withoutEmptyTail = (value: Descendant[]) => {
            let end = value.length;
            while (end) {
                const last = value[end - 1];
                if (!ElementApi.isElement(last) || last.type !== "p" || !last.children.every((child) => TextApi.isText(child) && !child.text)) break;
                end--;
            }
            return value.slice(0, end);
        };
        const initial = markdown.serialize({ value: withoutEmptyTail(markdown.deserialize(source)) });
        const live = markdown.serialize({ value: withoutEmptyTail(editor.children) });
        if (initial.trimEnd() === live.trimEnd()) return source;
    } catch {
        /* The live collaborative document wins when equivalence is unknown. */
    }
}

// Apply text/structural deltas through Slate operations; never reset a Yjs document.
// Untouched nodes, IDs, history and remote selection anchors stay in the live tree.
export function applySourceValue(editor: SlateEditor, next: Value) {
    type Node = Descendant;
    const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
    const reconcile = (before: Node[], after: Node[], parent: number[]) => {
        let suffix = 0;
        while (suffix < before.length && suffix < after.length && equal(before[before.length - suffix - 1], after[after.length - suffix - 1]))
            suffix++;
        const count = Math.min(before.length - suffix, after.length - suffix);
        for (let index = 0; index < count; index++) {
            const old = before[index],
                node = after[index],
                path = [...parent, index];
            if (equal(old, node)) continue;
            if (TextApi.isText(old) && TextApi.isText(node)) {
                let prefix = 0,
                    tail = 0;
                while (prefix < old.text.length && prefix < node.text.length && old.text[prefix] === node.text[prefix]) prefix++;
                while (
                    tail < old.text.length - prefix &&
                    tail < node.text.length - prefix &&
                    old.text[old.text.length - tail - 1] === node.text[node.text.length - tail - 1]
                )
                    tail++;
                const removed = old.text.slice(prefix, old.text.length - tail),
                    inserted = node.text.slice(prefix, node.text.length - tail);
                if (removed) editor.tf.apply({ type: "remove_text", path, offset: prefix, text: removed });
                if (inserted) editor.tf.apply({ type: "insert_text", path, offset: prefix, text: inserted });
            } else if (ElementApi.isElement(old) && ElementApi.isElement(node) && old.type === node.type) {
                reconcile([...old.children], node.children, path);
            } else {
                editor.tf.apply({ type: "remove_node", path, node: old });
                editor.tf.apply({ type: "insert_node", path, node });
                continue;
            }
            const properties: Record<string, unknown> = {},
                newProperties: Record<string, unknown> = {};
            for (const key of new Set([...Object.keys(old), ...Object.keys(node)])) {
                if (["children", "text", "id"].includes(key) || equal(old[key], node[key])) continue;
                properties[key] = old[key] ?? null;
                newProperties[key] = node[key] ?? null;
            }
            if (Object.keys(properties).length) editor.tf.apply({ type: "set_node", path, properties, newProperties });
        }
        for (let index = before.length - suffix - 1; index >= count; index--)
            editor.tf.apply({ type: "remove_node", path: [...parent, index], node: before[index] });
        for (let index = count; index < after.length - suffix; index++)
            editor.tf.apply({ type: "insert_node", path: [...parent, index], node: after[index] });
    };
    editor.tf.withoutNormalizing(() => reconcile([...editor.children], next, []));
}
