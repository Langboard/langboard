import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";

export type CloudFolder = "inbox" | "documents" | "media";
export interface CloudSource {
    kind: "project" | "card" | "wiki";
    project_uid: string;
    title: string;
    url: string;
    card_uid?: string;
    wiki_uid?: string;
}
export interface CloudConflict {
    source?: CloudSource;
    uid: string;
    name: string;
    same_name: boolean;
    same_content: boolean;
    can_replace: boolean;
}
interface Confirmation {
    confirmation_required: true;
    confirmation_token: string;
    request_uid: string;
    conflicts: CloudConflict[];
}
interface Attempt {
    file: File;
    requestUID: string;
    folder: CloudFolder;
    action?: "add" | "replace" | "cancel";
    replacementUID?: string;
    replacementSource?: CloudSource["kind"];
    replacementParentUID?: string;
    token?: string;
}
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const nonempty = (value: unknown): value is string => typeof value === "string" && value.length > 0;
function confirmation(value: unknown, uid: string): value is Confirmation {
    return (
        record(value) &&
        value.confirmation_required === true &&
        value.request_uid === uid &&
        nonempty(value.confirmation_token) &&
        Array.isArray(value.conflicts) &&
        value.conflicts.length <= 100 &&
        value.conflicts.every(
            (item: unknown) =>
                record(item) &&
                nonempty(item.uid) &&
                typeof item.name === "string" &&
                typeof item.same_name === "boolean" &&
                typeof item.same_content === "boolean" &&
                typeof item.can_replace === "boolean" &&
                (item.source === undefined ||
                    (record(item.source) &&
                        ["project", "card", "wiki"].includes(item.source.kind as string) &&
                        nonempty(item.source.project_uid) &&
                        typeof item.source.title === "string" &&
                        nonempty(item.source.url)))
        )
    );
}

/** A mounted actor/project source serializes files and retains exact retry payloads. */
export default function useCloudUpload(projectUID: string) {
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const client = useQueryClient();
    const queue = useRef<Attempt[]>([]);
    const controller = useRef<AbortController | undefined>(undefined);
    const active = useRef(false);
    const mounted = useRef(false);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState(false);
    const [prompt, setPrompt] = useState<Confirmation>();
    const [filename, setFilename] = useState("");
    const current = () =>
        mounted.current && useAuthStore.getState().currentUser?.uid === actor && useAuthStore.getState().getIdentityVersion() === identity;
    useEffect(() => {
        mounted.current = true;
        const stop = () => {
            controller.current?.abort();
            queue.current = [];
            setPrompt(undefined);
            setError(false);
            setPending(false);
        };
        const unsubscribe = useAuthStore.subscribe(() => {
            if (!current()) stop();
        });
        return () => {
            mounted.current = false;
            controller.current?.abort();
            queue.current = [];
            unsubscribe();
        };
    }, [projectUID, actor, identity]);
    async function run() {
        if (active.current || !current() || !actor) return;
        active.current = true;
        setPending(true);
        setError(false);
        controller.current = new AbortController();
        try {
            while (queue.current.length && current()) {
                const attempt = queue.current[0];
                setFilename(attempt.file.name);
                const form = new FormData();
                form.append("attachment", attempt.file);
                form.append("request_uid", attempt.requestUID);
                form.append("folder_id", attempt.folder);
                if (attempt.action) form.append("duplicate_action", attempt.action);
                if (attempt.token) form.append("confirmation_token", attempt.token);
                if (attempt.replacementUID) form.append("replacement_uid", attempt.replacementUID);
                if (attempt.replacementSource) form.append("replacement_source", attempt.replacementSource);
                const response = await api.post<unknown>(`/board/${encodeURIComponent(projectUID)}/cloud/attachment`, form, {
                    signal: controller.current.signal,
                    env: { interceptToast: true } as never,
                    validateStatus: (status) => [200, 201, 409].includes(status),
                });
                if (!current()) return;
                if (response.status === 409) {
                    if (!confirmation(response.data, attempt.requestUID)) throw new Error("Invalid Cloud confirmation");
                    attempt.action = undefined;
                    attempt.replacementUID = undefined;
                    attempt.replacementSource = undefined;
                    attempt.replacementParentUID = undefined;
                    attempt.token = response.data.confirmation_token;
                    setPrompt(response.data);
                    return;
                }
                const data = response.data;
                if (!record(data) || data.request_uid !== attempt.requestUID) throw new Error("Invalid Cloud receipt");
                if (attempt.action === "cancel") {
                    if (data.cancelled !== true) throw new Error("Invalid Cloud cancellation");
                } else {
                    if (
                        data.project_uid !== projectUID ||
                        data.folder_id !== attempt.folder ||
                        !nonempty(data.uid) ||
                        !nonempty(data.name) ||
                        !nonempty(data.url) ||
                        typeof data.replayed !== "boolean" ||
                        typeof data.created_at !== "string" ||
                        !Number.isFinite(Date.parse(data.created_at)) ||
                        typeof data.updated_at !== "string" ||
                        !Number.isFinite(Date.parse(data.updated_at)) ||
                        data.cancelled === true ||
                        data.confirmation_required === true ||
                        (attempt.replacementSource === "card"
                            ? !record(data.source) ||
                              data.source.kind !== "card" ||
                              !nonempty(data.card_uid) ||
                              data.card_uid !== data.source.card_uid ||
                              data.source.card_uid !== attempt.replacementParentUID
                            : attempt.replacementSource === "wiki"
                              ? !record(data.source) ||
                                data.source.kind !== "wiki" ||
                                !nonempty(data.source.wiki_uid) ||
                                data.source.wiki_uid !== attempt.replacementParentUID
                              : data.card_uid || data.wiki_uid) ||
                        (attempt.action === "replace" && data.uid !== attempt.replacementUID)
                    )
                        throw new Error("Invalid Cloud attachment");
                    void client.invalidateQueries({ queryKey: ["board-cloud", actor, identity, projectUID] });
                }
                queue.current.shift();
            }
        } catch {
            // An uncertain result retains the exact file, UUID, folder and decision for retry.
            if (current()) setError(true);
        } finally {
            active.current = false;
            if (current()) setPending(false);
        }
    }
    function start(files: File[], folder: CloudFolder) {
        if (queue.current.length || !current()) return;
        queue.current = files.map((file) => ({ file, folder, requestUID: crypto.randomUUID() }));
        void run();
    }
    function decide(action: "add" | "replace" | "cancel", replacementUID?: string, replacementSource?: CloudSource["kind"]) {
        const attempt = queue.current[0];
        if (
            !attempt ||
            !prompt ||
            !current() ||
            (action === "replace" &&
                !prompt.conflicts.some((item) => item.uid === replacementUID && item.source?.kind === replacementSource && item.can_replace))
        )
            return;
        attempt.action = action;
        attempt.replacementUID = action === "replace" ? replacementUID : undefined;
        attempt.replacementSource = action === "replace" ? replacementSource : undefined;
        const selected = prompt.conflicts.find((item) => item.uid === replacementUID && item.source?.kind === replacementSource);
        attempt.replacementParentUID = action === "replace" ? (selected?.source?.card_uid ?? selected?.source?.wiki_uid) : undefined;
        setPrompt(undefined);
        void run();
    }
    function cancelQueue() {
        if (pending) return;
        queue.current = [];
        setPrompt(undefined);
        setError(false);
    }
    return { start, retry: run, decide, cancelQueue, pending, error, prompt, filename, busy: pending || error || !!prompt };
}
