import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useEditorSelector, type PlateEditor } from "platejs/react";
import { MarkdownPlugin, markdownToAstProcessor } from "@platejs/markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { Value } from "platejs";
import { useTranslation } from "react-i18next";
import { MarkdownSourcePlugin, applySourceValue } from "./markdown-source-plugin";
import { mapSource, pointSource, sourceKey, sourcePoint, mergeSource, type AstNode, type DocumentNode } from "./source-model";

interface Props {
    editor: PlateEditor;
    children: ReactNode;
    initialSource?: string;
    disabled: boolean;
    draftKey: string;
    previewFirst?: boolean;
    onSourceChange: () => void;
}

export function MarkdownSourceSurface({ editor, children, initialSource, disabled, draftKey, previewFirst = false, onSourceChange }: Props) {
    const [t] = useTranslation();
    const markdown = editor.getApi(MarkdownPlugin).markdown;
    const signature = useEditorSelector((current) => JSON.stringify(current.children), []);
    const [source, setSource] = useState(() => {
        if (initialSource !== undefined) {
            const clean = (value: unknown) => JSON.stringify(value, (key, item) => (key === "id" ? undefined : item));
            try {
                if (clean(markdown.deserialize(initialSource)) === clean(editor.children)) {
                    editor.getApi(MarkdownSourcePlugin).markdownSource.remember(initialSource);
                    return initialSource;
                }
            } catch {
                /* The live document remains authoritative when parsing is incomplete. */
            }
        }
        return markdown.serialize();
    });
    const textarea = useRef<HTMLTextAreaElement>(null);
    const preview = useRef<HTMLDivElement>(null);
    const sourcePane = useRef<HTMLDivElement>(null);
    const previousLayout = useRef<number[]>([]);
    useLayoutEffect(() => {
        const panes = [preview.current, sourcePane.current];
        const positions = panes.map((pane) => pane?.getBoundingClientRect().left ?? 0);
        const animations: Animation[] = [];
        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            panes.forEach((pane, index) => {
                if (!pane?.animate) return;
                const delta = (previousLayout.current[index] ?? positions[index]) - positions[index];
                animations.push(
                    pane.animate(
                        previousLayout.current.length
                            ? [{ transform: `translateX(${delta}px)` }, { transform: "translateX(0)" }]
                            : [{ opacity: 0 }, { opacity: 1 }],
                        { duration: previousLayout.current.length ? 140 : 120, easing: "ease-out" }
                    )
                );
            });
        }
        previousLayout.current = positions;
        return () => animations.forEach((animation) => animation.cancel());
    }, [previewFirst]);
    const mirror = useRef<HTMLPreElement>(null);
    const composing = useRef(false);
    const compositionBase = useRef("");
    const localSnapshot = useRef(JSON.stringify(editor.children));
    const blocked = useRef<"source" | "preview" | null>(null);
    const releaseFrame = useRef(0);
    const selected = useRef({ start: 0, end: 0 });
    const history = useRef([{ value: source, start: 0, end: 0 }]);
    const historyIndex = useRef(0);
    const pendingSelection = useRef(false);
    const restoredSelection = useRef(false);
    const [parseFailed, setParseFailed] = useState(false);
    const [conflict, setConflict] = useState<string | null>(null);
    const [caret, setCaret] = useState<{ left: number; top: number; height: number } | null>(null);
    useEffect(() => {
        try {
            const recovered = localStorage.getItem(draftKey);
            if (recovered !== null) {
                setSource(recovered);
                setConflict(markdown.serialize());
            }
        } catch {
            /* Recovery remains in memory when storage is unavailable. */
        }
    }, [draftKey, markdown]);
    const map = useMemo(() => {
        try {
            const ast = markdownToAstProcessor(editor, source, { remarkPlugins: [remarkGfm, remarkMath] });
            return mapSource(source, ast as AstNode, editor.children as DocumentNode[]);
        } catch {
            return [];
        }
    }, [editor, source, signature]);

    // Remote/AI edits are observed independently from local raw edits. Do not
    // rewrite the textarea during IME composition; reconcile after compositionend.
    useEffect(() => {
        if (composing.current || localSnapshot.current === signature) return;
        localSnapshot.current = signature;
        if (conflict !== null || parseFailed) {
            setConflict(markdown.serialize());
            try {
                localStorage.setItem(draftKey, source);
            } catch {
                /* Keep the draft visible. */
            }
        } else {
            const remote = markdown.serialize();
            setSource(remote);
            history.current = [{ value: remote, start: 0, end: 0 }];
            historyIndex.current = 0;
        }
    }, [signature, markdown, conflict, parseFailed, draftKey, source]);
    const lockScroll = (side: "source" | "preview") => {
        blocked.current = side;
        cancelAnimationFrame(releaseFrame.current);
        releaseFrame.current = requestAnimationFrame(() => {
            blocked.current = null;
        });
    };
    useEffect(() => () => cancelAnimationFrame(releaseFrame.current), []);

    // A textarea's DOM range is not measurable. Use the same width/font/wrapping
    // in an inert mirror to measure original-source offsets, including soft wraps.
    const sourceY = useCallback(
        (offset: number) => {
            const pre = mirror.current,
                input = textarea.current;
            if (!pre || !input) return 0;
            pre.style.width = `${input.clientWidth}px`;
            pre.replaceChildren(document.createTextNode(source.slice(0, offset)));
            const marker = document.createElement("span");
            marker.textContent = "\u200b";
            pre.appendChild(marker);
            return marker.offsetTop;
        },
        [source]
    );
    const previewY = useCallback(
        (offset: number) => {
            const point = sourcePoint(map, offset),
                root = preview.current;
            if (!point || !root) return undefined;
            try {
                const range = editor.api.toDOMRange({ anchor: point, focus: point });
                if (!range) return undefined;
                return range.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop;
            } catch {
                return undefined;
            }
        },
        [editor, map]
    );
    const syncFromSource = () => {
        const input = textarea.current,
            root = preview.current;
        if (!input || !root || blocked.current === "source") return;
        let low = 0,
            high = source.length;
        while (low < high) {
            const mid = Math.floor((low + high) / 2);
            if (sourceY(mid) < input.scrollTop) low = mid + 1;
            else high = mid;
        }
        const y = previewY(low);
        if (y === undefined) return;
        lockScroll("preview");
        if (input.scrollTop + input.clientHeight >= input.scrollHeight - 2) root.scrollTop = root.scrollHeight;
        else root.scrollTop = y;
    };
    const syncFromPreview = () => {
        const input = textarea.current,
            root = preview.current;
        if (!input || !root || blocked.current === "preview") return;
        const rect = root.getBoundingClientRect();
        const doc = document as Document & { caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null };
        const caret = doc.caretPositionFromPoint?.(rect.left + 16, rect.top + 8);
        const fallback = document.caretRangeFromPoint?.(rect.left + 16, rect.top + 8);
        const node = caret?.offsetNode ?? fallback?.startContainer;
        const offset = caret?.offset ?? fallback?.startOffset;
        if (!node || offset === undefined || !root.contains(node)) return;
        try {
            const point = editor.api.toSlatePoint([node, offset], { exactMatch: false });
            const sourceOffset = point && pointSource(map, point);
            if (typeof sourceOffset !== "number") return;
            lockScroll("source");
            input.scrollTop = root.scrollTop + root.clientHeight >= root.scrollHeight - 2 ? input.scrollHeight : sourceY(sourceOffset);
        } catch {
            /* A remote update may have replaced this DOM point. */
        }
    };
    const highlight = () => {
        const input = textarea.current,
            root = preview.current;
        if (!input || !root || composing.current) return;
        selected.current = { start: input.selectionStart, end: input.selectionEnd };
        const current = history.current[historyIndex.current];
        if (current.value === source) Object.assign(current, selected.current);
        root.querySelectorAll("[data-source-active]").forEach((el) => el.removeAttribute("data-source-active"));
        const point = sourcePoint(map, input.selectionStart);
        if (!point) return;
        try {
            let [node] = editor.api.node(point.path.slice(0, 1))!;
            for (let depth = point.path.length - 1; depth > 1; depth--) {
                const [ancestor] = editor.api.node(point.path.slice(0, depth))!;
                if (ancestor.type === "td" || ancestor.type === "th") {
                    node = ancestor;
                    break;
                }
            }
            const el = editor.api.toDOMNode(node);
            el?.setAttribute("data-source-active", "true");
            // Keep a real Plate selection so switching modes restores the caret.
            editor.tf.select({ anchor: point, focus: sourcePoint(map, input.selectionEnd) ?? point });
            const domRange = editor.api.toDOMRange({ anchor: point, focus: point });
            if (!domRange) return;
            const rect = domRange.getBoundingClientRect();
            const rootRect = root.getBoundingClientRect();
            setCaret(
                input.selectionStart === input.selectionEnd
                    ? {
                          left: rect.left - rootRect.left + root.scrollLeft,
                          top: rect.top - rootRect.top + root.scrollTop,
                          height: rect.height || 20,
                      }
                    : null
            );
            const y = previewY(input.selectionStart);
            if (y !== undefined && (y < root.scrollTop || y > root.scrollTop + root.clientHeight - 24)) {
                lockScroll("preview");
                root.scrollTop = Math.max(0, y - root.clientHeight / 2);
            }
        } catch {
            /* Selection may be invalidated by a concurrent remote edit. */
        }
    };
    useLayoutEffect(() => {
        if (!restoredSelection.current && map.length && editor.selection) {
            restoredSelection.current = true;
            const start = pointSource(map, editor.selection.anchor),
                end = pointSource(map, editor.selection.focus);
            if (start !== undefined && end !== undefined) {
                selected.current = { start: Math.min(start, end), end: Math.max(start, end) };
                pendingSelection.current = true;
            }
        }
        if (pendingSelection.current && textarea.current) {
            textarea.current.setSelectionRange(selected.current.start, selected.current.end);
            pendingSelection.current = false;
        }
        if (document.activeElement === textarea.current) highlight();
    }, [source, map]);
    const commit = (text: string, record = true) => {
        setSource(text);
        if (composing.current) return;
        if (conflict !== null) {
            try {
                localStorage.setItem(draftKey, text);
            } catch {
                /* Keep the draft visible. */
            }
            return;
        }
        if (record && history.current[historyIndex.current].value !== text) {
            history.current.splice(historyIndex.current + 1);
            history.current.push({ value: text, ...selected.current });
            if (history.current.length > 100) history.current.shift();
            historyIndex.current = history.current.length - 1;
        }
        try {
            const next = markdown.deserialize(text);
            applySourceValue(editor, (next.length ? next : [{ type: "p", children: [{ text: "" }] }]) as Value);
            localSnapshot.current = JSON.stringify(editor.children);
            editor.getApi(MarkdownSourcePlugin).markdownSource.remember(text);
            setParseFailed(false);
            onSourceChange();
        } catch {
            // Keep the exact draft recoverable even while Markdown is incomplete.
            editor.getApi(MarkdownSourcePlugin).markdownSource.remember(text);
            localSnapshot.current = JSON.stringify(editor.children);
            setParseFailed(true);
            onSourceChange();
        }
    };
    const undoSource = (redo: boolean) => {
        const index = historyIndex.current + (redo ? 1 : -1);
        if (index < 0 || index >= history.current.length) return;
        historyIndex.current = index;
        const item = history.current[index];
        selected.current = { start: item.start, end: item.end };
        pendingSelection.current = true;
        commit(item.value, false);
    };
    return (
        <div className="relative md:grid md:grid-cols-2" data-markdown-source="true" data-preview-first={previewFirst}>
            <div
                ref={preview}
                aria-label={t("editor.Markdown preview")}
                className={[
                    "relative max-h-[60vh] min-h-48 overflow-auto",
                    "[&_[data-source-active]]:bg-accent/60 [&_[role=toolbar]]:hidden",
                    previewFirst ? "md:order-1 md:border-r" : "md:order-2 md:border-l",
                ].join(" ")}
                onScroll={syncFromPreview}
                onLoadCapture={() => {
                    if (document.activeElement === textarea.current) highlight();
                }}
                onMouseUp={() => {
                    const selection = window.getSelection();
                    if (!selection?.anchorNode || !selection.focusNode || !preview.current?.contains(selection.anchorNode)) return;
                    try {
                        const point = editor.api.toSlatePoint([selection.anchorNode, selection.anchorOffset], { exactMatch: false });
                        const focus = editor.api.toSlatePoint([selection.focusNode, selection.focusOffset], { exactMatch: false });
                        const offset = point && pointSource(map, point);
                        const end = focus && pointSource(map, focus);
                        if (typeof offset !== "number" || !textarea.current) return;
                        textarea.current.focus();
                        const focusOffset = typeof end === "number" ? end : offset;
                        textarea.current.setSelectionRange(
                            Math.min(offset, focusOffset),
                            Math.max(offset, focusOffset),
                            offset > focusOffset ? "backward" : "forward"
                        );
                        textarea.current.scrollTop = Math.max(0, sourceY(offset) - textarea.current.clientHeight / 2);
                    } catch {
                        /* Non-text media uses its normal preview interaction. */
                    }
                }}
            >
                {children}
                {caret && (
                    <span
                        aria-hidden="true"
                        className="pointer-events-none absolute w-px bg-primary"
                        style={{ left: caret.left, top: caret.top, height: caret.height }}
                    />
                )}
            </div>
            <div ref={sourcePane} className={`relative min-w-0 ${previewFirst ? "md:order-2" : "md:order-1"}`}>
                <textarea
                    ref={textarea}
                    aria-label={t("editor.Markdown source")}
                    value={source}
                    disabled={disabled}
                    spellCheck={false}
                    className={[
                        "block h-[60vh] min-h-48 w-full resize-none whitespace-pre-wrap break-words bg-background p-4",
                        "font-mono text-sm leading-6 outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    ].join(" ")}
                    onChange={(event) => commit(event.target.value)}
                    onCompositionStart={() => {
                        composing.current = true;
                        compositionBase.current = source;
                    }}
                    onCompositionEnd={(event) => {
                        composing.current = false;
                        const text = event.currentTarget.value;
                        if (localSnapshot.current !== JSON.stringify(editor.children)) {
                            const remote = markdown.serialize();
                            const merged = mergeSource(compositionBase.current, text, remote);
                            if (merged === undefined) {
                                setSource(text);
                                setConflict(remote);
                                try {
                                    localStorage.setItem(draftKey, text);
                                } catch {
                                    /* Keep the draft visible. */
                                }
                                return;
                            }
                            commit(merged);
                        } else commit(text);
                    }}
                    onSelect={highlight}
                    onScroll={syncFromSource}
                    onKeyDown={(event) => {
                        if (event.nativeEvent.isComposing || composing.current) return;
                        const mod = event.metaKey || event.ctrlKey;
                        if (mod && (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y")) {
                            event.preventDefault();
                            event.stopPropagation();
                            undoSource(event.shiftKey || event.key.toLowerCase() === "y");
                            return;
                        }
                        const input = event.currentTarget;
                        const edit = sourceKey(
                            source,
                            input.selectionStart,
                            input.selectionEnd,
                            event.key,
                            event.metaKey || event.ctrlKey,
                            event.shiftKey
                        );
                        if (!edit) return;
                        event.preventDefault();
                        selected.current = { start: edit.start, end: edit.end };
                        pendingSelection.current = true;
                        commit(edit.value);
                    }}
                />
                <pre
                    ref={mirror}
                    aria-hidden="true"
                    className="pointer-events-none invisible absolute left-0 top-0 whitespace-pre-wrap break-words p-4 font-mono text-sm leading-6"
                />
                {parseFailed && (
                    <div role="status" className="p-2 text-sm text-muted-foreground">
                        {t("editor.Markdown draft preserved")}
                    </div>
                )}
                {conflict !== null && (
                    <div role="alert" className="space-y-2 border-t p-3 text-sm">
                        <p>{t("editor.Markdown draft conflict")}</p>
                        <button
                            type="button"
                            className="rounded border px-2 py-1"
                            onClick={() => {
                                const remote = markdown.serialize();
                                setConflict(null);
                                setSource(remote);
                                localSnapshot.current = JSON.stringify(editor.children);
                                try {
                                    localStorage.removeItem(draftKey);
                                } catch {
                                    /* Storage may be restricted. */
                                }
                            }}
                        >
                            {t("editor.Use current document")}
                        </button>
                        <button
                            type="button"
                            className="ml-2 rounded border px-2 py-1"
                            onClick={() => {
                                const text = source;
                                const next = markdown.deserialize(text);
                                applySourceValue(editor, (next.length ? next : [{ type: "p", children: [{ text: "" }] }]) as Value);
                                editor.getApi(MarkdownSourcePlugin).markdownSource.remember(text);
                                localSnapshot.current = JSON.stringify(editor.children);
                                setConflict(null);
                                onSourceChange();
                                try {
                                    localStorage.removeItem(draftKey);
                                } catch {
                                    /* Storage may be restricted. */
                                }
                            }}
                        >
                            {t("editor.Apply my draft")}
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
