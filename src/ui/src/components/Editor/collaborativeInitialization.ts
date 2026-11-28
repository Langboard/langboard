import * as Y from "yjs";
import { YjsEditor, yTextToSlateElement } from "@slate-yjs/core";
import type { Value } from "platejs";

export const getCollaborativeDocumentValue = (doc: Y.Doc): Value => {
    const root = doc.get("content", Y.XmlText);
    return (root.length ? yTextToSlateElement(root).children : []) as Value;
};

export const deferCollaborativeConnection = (editor: unknown) => {
    if (!YjsEditor.isYjsEditor(editor)) throw new Error("The collaborative editor binding is unavailable.");
    const connect = editor.connect;
    let requested = false;
    let finished = false;
    let initializing = false;
    const defer = () => {
        requested = true;
    };
    // Providers still synchronize the native Y.Doc. Keep its Slate binding
    // disconnected until owner arbitration so empty shared state cannot publish
    // the native top-level text sentinel to React or normalize a local seed.
    editor.connect = defer;
    const restore = () => {
        if (editor.connect === defer) editor.connect = connect;
    };
    return {
        initialize: async (initialize: () => Promise<void>) => {
            if (finished) return;
            initializing = true;
            try {
                await initialize();
            } finally {
                initializing = false;
                if (finished) restore();
            }
        },
        resume: () => {
            if (finished) return;
            finished = true;
            restore();
            if (requested && !YjsEditor.connected(editor)) YjsEditor.connect(editor);
        },
        dispose: () => {
            finished = true;
            // Native init can call connect after its initial-sync timeout.
            // Keep that late call fenced until this initialization settles.
            if (!initializing) restore();
        },
    };
};

export interface ICollaborativeRestoreRequest {
    document_name: string;
    value: string;
    restore_snapshot: string;
}

export const initializeCollaborativeDraft = async ({
    documentID,
    doc,
    value,
    currentValue,
    restore,
    signal,
}: {
    documentID: string;
    doc: Y.Doc;
    value: string;
    currentValue: string;
    restore: (request: ICollaborativeRestoreRequest, signal?: AbortSignal) => Promise<boolean | "busy">;
    signal?: AbortSignal;
}): Promise<boolean> => {
    if (doc.get("content", Y.XmlText).length && currentValue === value) {
        return true;
    }

    // Capture once after initial sync. A collision must never rebase onto newer
    // remote work or fall back to independent local replacement operations.
    const snapshot = Y.encodeStateAsUpdate(doc);
    let updated = false;
    let resolveUpdate: () => void;
    const ownerUpdate = new Promise<void>((resolve) => {
        resolveUpdate = resolve;
    });
    const onUpdate = () => {
        updated = true;
        resolveUpdate();
    };
    doc.on("update", onUpdate);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let onAbort: (() => void) | undefined;
    try {
        signal?.throwIfAborted();
        const result = await restore(
            {
                document_name: documentID,
                value,
                restore_snapshot: btoa(Array.from(snapshot, (byte) => String.fromCharCode(byte)).join("")),
            },
            signal
        );
        if (result === false) return false;
        if (!updated) {
            // A busy loser waits for the winning owner's update. It must not
            // normalize an empty document and invalidate that pending patch.
            await Promise.race([
                ownerUpdate,
                new Promise<never>((_resolve, reject) => {
                    onAbort = () => reject(new Error("Collaborative initialization ended before the owner update."));
                    signal?.addEventListener("abort", onAbort, { once: true });
                    if (signal?.aborted) onAbort();
                    timer = setTimeout(() => reject(new Error("The restored owner update did not arrive.")), 10_000);
                }),
            ]);
        }
        return true;
    } finally {
        doc.off("update", onUpdate);
        if (timer !== undefined) clearTimeout(timer);
        if (onAbort) signal?.removeEventListener("abort", onAbort);
    }
};
