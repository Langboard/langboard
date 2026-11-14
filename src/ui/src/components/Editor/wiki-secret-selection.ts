import type { Value, TRange } from "platejs";

export interface WikiSelectionEditor {
    children: Value;
    selection: TRange | null;
    api: { markdown: { serialize: (options?: { value: Value }) => string } };
}
export interface WikiSecretSelection {
    start_utf16: number;
    end_utf16: number;
}

/** Map only a literal text leaf, with a verified exact Markdown round trip. */
export function mapWikiSecretSelection(editor: WikiSelectionEditor, source: string): WikiSecretSelection | null {
    const range = editor.selection;
    if (!range || JSON.stringify(range.anchor.path) !== JSON.stringify(range.focus.path)) return null;
    const path = range.anchor.path;
    if (path.length !== 2) return null;
    const block = editor.children[path[0]];
    if (!block || block.type !== "p" || block.children.length !== 1 || path[1] !== 0) return null;
    const leaf = block.children[0];
    if (typeof leaf.text !== "string" || Object.keys(leaf).some((key) => key !== "text")) return null;
    const text = leaf.text;
    const start = Math.min(range.anchor.offset, range.focus.offset);
    const end = Math.max(range.anchor.offset, range.focus.offset);
    const boundary = (offset: number) =>
        offset === 0 || offset === text.length || !(/[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset]));
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > text.length || start >= end || !boundary(start) || !boundary(end))
        return null;
    // The backend supports literal single lines. Rich structures and escaping require a future contract.
    if (text.includes("secret://")) return null;
    if (/[\n\r\\`*_\[\]<>~|]/.test(leaf.text) || /^\s*(?:[#>+\-]|\d+[.)])(?:\s|$)/.test(leaf.text)) return null;
    const token = `WIKISELECTION${crypto.randomUUID().replaceAll("-", "")}`;
    const begin = `${token}BEGIN`;
    const finish = `${token}END`;
    if (source.includes(token)) return null;
    const value = structuredClone(editor.children);
    value[path[0]].children[0].text = leaf.text.slice(0, start) + begin + leaf.text.slice(start, end) + finish + leaf.text.slice(end);
    try {
        const original = editor.api.markdown.serialize({ value: structuredClone(editor.children) });
        const marked = editor.api.markdown.serialize({ value });
        // Native Markdown emits one terminal LF. Accept its removal only when the exact source proves it.
        const terminal = original === source ? "" : original === `${source}\n` ? "\n" : null;
        if (terminal === null || (terminal && !marked.endsWith(terminal))) return null;
        const serialized = terminal ? marked.slice(0, -1) : marked;
        if (serialized.split(begin).length !== 2 || serialized.split(finish).length !== 2) return null;
        const start_utf16 = serialized.indexOf(begin);
        const end_utf16 = serialized.indexOf(finish) - begin.length;
        if (end_utf16 <= start_utf16 || serialized.replace(begin, "").replace(finish, "") !== source) return null;
        return { start_utf16, end_utf16 };
    } catch {
        return null;
    }
}

export async function wikiMarkdownRevision(source: string): Promise<string> {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
