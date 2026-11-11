export interface SourcePoint {
    path: number[];
    offset: number;
}
export interface SourceCharacter {
    source: number;
    point: SourcePoint;
}
export interface AstNode {
    type: string;
    value?: string;
    children?: AstNode[];
    position?: { start: { offset?: number }; end: { offset?: number } };
}
export interface DocumentNode {
    text?: string;
    children?: DocumentNode[];
    [key: string]: unknown;
}

// TOAST's AST-node/DOM mapping translated to Slate paths. Offsets always refer to
// the original source, before the application's Markdown escape preprocessing.
export function mapSource(source: string, ast: AstNode, nodes: DocumentNode[]): SourceCharacter[] {
    const visible: { text: string; offsets: number[] } = { text: "", offsets: [] };
    const visit = (node: AstNode) => {
        if (node.children) {
            node.children.forEach(visit);
            visible.text += "\n";
            visible.offsets.push(node.position?.end.offset ?? source.length);
            return;
        }
        if (node.value === undefined || node.position?.start.offset === undefined) return;
        let cursor = node.position.start.offset;
        const end = node.position.end.offset ?? source.length;
        for (const ch of node.value.split("")) {
            const found = source.indexOf(ch, cursor);
            const offset = found >= cursor && found < end ? found : cursor;
            visible.text += ch;
            visible.offsets.push(Math.min(offset, end));
            const entity = /^&(?:#\d+|#x[0-9a-f]+|amp|lt|gt|quot|apos|nbsp);/i.exec(source.slice(offset, end));
            cursor = Math.min(offset + (entity ? entity[0].length : 1), end);
        }
    };
    visit(ast);
    const result: SourceCharacter[] = [];
    let cursor = 0;
    const walk = (node: DocumentNode, path: number[]) => {
        if (typeof node.text === "string") {
            const found = visible.text.indexOf(node.text, cursor);
            if (found < 0 || !node.text.length) return;
            for (let i = 0; i <= node.text.length; i++) {
                const sourceOffset = i === node.text.length ? (visible.offsets[found + i - 1] ?? 0) + 1 : visible.offsets[found + i];
                result.push({ source: Math.min(sourceOffset, source.length), point: { path, offset: i } });
            }
            cursor = found + node.text.length;
        } else node.children?.forEach((child, index) => walk(child, [...path, index]));
    };
    nodes.forEach((node, index) => walk(node, [index]));
    return result;
}

export function sourcePoint(map: SourceCharacter[], offset: number): SourcePoint | undefined {
    let nearest: SourceCharacter | undefined;
    for (const entry of map) if (!nearest || Math.abs(entry.source - offset) < Math.abs(nearest.source - offset)) nearest = entry;
    return nearest?.point;
}

export function pointSource(map: SourceCharacter[], point: SourcePoint): number | undefined {
    return map.find((entry) => entry.point.offset === point.offset && entry.point.path.join(".") === point.path.join("."))?.source;
}

export interface SourceEdit {
    value: string;
    start: number;
    end: number;
}

// Merge independent source changes without overwriting a remote edit that arrived
// during composition. An overlap requires an explicit choice by the editor user.
export function mergeSource(base: string, local: string, remote: string): string | undefined {
    if (base === remote || local === remote) return local;
    if (base === local) return remote;
    const change = (value: string) => {
        let start = 0,
            suffix = 0;
        while (start < base.length && start < value.length && base[start] === value[start]) start++;
        while (suffix < base.length - start && suffix < value.length - start && base[base.length - suffix - 1] === value[value.length - suffix - 1])
            suffix++;
        return { start, end: base.length - suffix, text: value.slice(start, value.length - suffix) };
    };
    const a = change(local),
        b = change(remote);
    if (a.start === b.start || (a.start < b.end && b.start < a.end)) return;
    const first = a.start < b.start ? a : b,
        last = first === a ? b : a;
    return base.slice(0, first.start) + first.text + base.slice(first.end, last.start) + last.text + base.slice(last.end);
}
export function sourceKey(value: string, start: number, end: number, key: string, mod = false, shift = false): SourceEdit | undefined {
    const replace = (text: string, a = start, b = end, selectionStart = a + text.length, selectionEnd = selectionStart) => ({
        value: value.slice(0, a) + text + value.slice(b),
        start: selectionStart,
        end: selectionEnd,
    });
    const delimiter = mod ? ({ b: "**", i: "_", e: "`" } as Record<string, string>)[key.toLowerCase()] : undefined;
    if (delimiter) {
        if (value.slice(start - delimiter.length, start) === delimiter && value.slice(end, end + delimiter.length) === delimiter) {
            return replace(
                value.slice(start, end),
                start - delimiter.length,
                end + delimiter.length,
                start - delimiter.length,
                end - delimiter.length
            );
        }
        return replace(delimiter + value.slice(start, end) + delimiter, start, end, start + delimiter.length, end + delimiter.length);
    }
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    if (key === "Tab") {
        const lineEnd = end > start && value[end - 1] === "\n" ? end - 1 : end;
        const last = value.indexOf("\n", lineEnd);
        const blockEnd = last < 0 ? value.length : last;
        const block = value.slice(lineStart, blockEnd);
        const changed = block
            .split("\n")
            .map((line) => (shift ? line.replace(/^( {1,4}|\t)/, "") : "    " + line))
            .join("\n");
        const firstDelta = changed.split("\n")[0].length - block.split("\n")[0].length;
        return replace(
            changed,
            lineStart,
            blockEnd,
            Math.max(lineStart, start + firstDelta),
            Math.max(lineStart, end + changed.length - block.length)
        );
    }
    if (key !== "Enter" || start !== end || mod || shift) return;
    // Do not continue list-looking text inside fenced code.
    const fences = value.slice(0, lineStart).match(/^\s*(`{3,}|~{3,})/gm);
    if (fences && fences.length % 2) return;
    const line = value.slice(lineStart, start);
    const match = /^(\s*)(?:(\d+)([.)])|([-+*])|(>))\s+(\[[ xX]\]\s+)?(.*)$/.exec(line);
    if (!match) return;
    if (!match[7].trim()) return replace("", lineStart, start);
    const marker = match[2] ? `${Number(match[2]) + 1}${match[3]}` : match[4] || match[5];
    return replace(`\n${match[1]}${marker} ${match[6] ? "[ ] " : ""}`);
}
