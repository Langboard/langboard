import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { isAxiosError } from "axios";
import type { AxiosProgressEvent } from "axios";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import useAuthStore, { getAuthStore } from "@/core/stores/AuthStore";

interface Conflict {
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
    conflicts: Conflict[];
}
export interface NativeAttachment {
    uid: string;
    card_uid: string;
    name: string;
    url: string;
    order: number;
    request_uid: string;
    created_at: string;
    updated_at: string;
}
type Choice = { action: "add" | "replace"; replacementUID?: string } | undefined;
interface Attempt {
    file: File;
    url: string;
    scope: string;
    identity: number;
    actor: string | undefined;
    requestUID: string;
    controller: AbortController;
    choice?: Choice;
    token?: string;
}
interface Prompt {
    attempt: Attempt;
    confirmation: Confirmation;
    resolve: (choice: Choice) => void;
    activate: () => void;
}

// Concurrent file sources share one accessible modal at a time. Each source
// retains its own request, React Dialog, and cancellation lifecycle.
const decisions: Prompt[] = [];
const removeDecision = (prompt: Prompt) => {
    const index = decisions.indexOf(prompt);
    if (index < 0) return;
    decisions.splice(index, 1);
    if (index === 0) queueMicrotask(() => decisions[0]?.activate());
};
const record = (data: unknown): data is Record<string, unknown> => typeof data === "object" && data !== null;
const nonempty = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const confirmation = (data: unknown, requestUID: string): data is Confirmation =>
    record(data) &&
    data.confirmation_required === true &&
    data.request_uid === requestUID &&
    nonempty(data.confirmation_token) &&
    Array.isArray(data.conflicts) &&
    data.conflicts.length <= 100 &&
    data.conflicts.every(
        (item: unknown) =>
            record(item) &&
            nonempty(item.uid) &&
            typeof item.name === "string" &&
            typeof item.same_name === "boolean" &&
            typeof item.same_content === "boolean" &&
            typeof item.can_replace === "boolean"
    );
const attachment = (data: unknown, requestUID: string, cardUID: string): data is NativeAttachment =>
    record(data) &&
    data.request_uid === requestUID &&
    data.card_uid === cardUID &&
    nonempty(data.uid) &&
    nonempty(data.name) &&
    nonempty(data.url) &&
    typeof data.order === "number" &&
    Number.isFinite(data.order) &&
    nonempty(data.created_at) &&
    Number.isFinite(Date.parse(data.created_at)) &&
    nonempty(data.updated_at) &&
    Number.isFinite(Date.parse(data.updated_at)) &&
    data.confirmation_required !== true &&
    data.cancelled !== true;

/** One mounted native upload source owns its file, decision, and retry identity. */
export function useNativeAttachmentUpload(scope: string) {
    const [t] = useTranslation();
    const [prompt, setPrompt] = useState<Prompt>();
    const promptRef = useRef<Prompt | undefined>(undefined);
    const attemptRef = useRef<Attempt | undefined>(undefined);
    const mountedRef = useRef(true);
    const sourceRef = useRef(scope);
    sourceRef.current = scope;
    const current = (attempt: Attempt) =>
        mountedRef.current &&
        attemptRef.current === attempt &&
        sourceRef.current === attempt.scope &&
        !attempt.controller.signal.aborted &&
        getAuthStore().getIdentityVersion() === attempt.identity &&
        getAuthStore().currentUser?.uid === attempt.actor;
    const cancel = () => {
        attemptRef.current?.controller.abort();
        attemptRef.current = undefined;
        const pending = promptRef.current;
        promptRef.current = undefined;
        if (pending) {
            removeDecision(pending);
            pending.resolve(undefined);
        }
    };
    useEffect(() => {
        mountedRef.current = true;
        // Account changes cancel immediately, even while this source is awaiting consent.
        const unsubscribe = useAuthStore.subscribe(() => {
            const attempt = attemptRef.current;
            if (attempt && !current(attempt)) {
                cancel();
                setPrompt(undefined);
            }
        });
        return () => {
            unsubscribe();
            mountedRef.current = false;
            if (sourceRef.current !== scope) {
                cancel();
            } else {
                // React StrictMode immediately reattaches effects; a real unmount stays inactive.
                queueMicrotask(() => {
                    if (!mountedRef.current) cancel();
                });
            }
        };
    }, [scope]);
    // A new scope must never render a previous source's confirmation.
    const visiblePrompt = prompt?.attempt.scope === scope && current(prompt.attempt) ? prompt : undefined;
    const decide = (choice: Choice) => {
        const pending = promptRef.current;
        if (!pending) return;
        promptRef.current = undefined;
        setPrompt(undefined);
        removeDecision(pending);
        pending.resolve(current(pending.attempt) ? choice : undefined);
    };
    async function upload(url: string, cardUID: string, file: File, onUploadProgress?: (event: AxiosProgressEvent) => void) {
        let attempt = attemptRef.current;
        if (!attempt || !current(attempt) || attempt.file !== file || attempt.url !== url) {
            cancel();
            attempt = {
                file,
                url,
                scope,
                identity: getAuthStore().getIdentityVersion(),
                actor: getAuthStore().currentUser?.uid,
                requestUID: crypto.randomUUID(),
                controller: new AbortController(),
            };
            attemptRef.current = attempt;
        }
        const active = attempt;
        try {
            while (current(active)) {
                const form = new FormData();
                form.append("attachment", active.file);
                form.append("request_uid", active.requestUID);
                if (active.choice) {
                    form.append("duplicate_action", active.choice.action);
                    form.append("confirmation_token", active.token!);
                    if (active.choice.action === "replace") form.append("replacement_uid", active.choice.replacementUID!);
                }
                const response = await api.post<unknown>(url, form, {
                    signal: active.controller.signal,
                    onUploadProgress: (event) => current(active) && onUploadProgress?.(event),
                    env: { interceptToast: true } as never,
                    validateStatus: (status) => (status >= 200 && status < 300) || status === 409,
                });
                if (!current(active)) return;
                if (response.status === 409) {
                    if (!confirmation(response.data, active.requestUID)) throw new Error("Invalid upload confirmation");
                    const decision = response.data;
                    active.choice = undefined;
                    active.token = decision.confirmation_token;
                    const choice = await new Promise<Choice>((resolve) => {
                        const next: Prompt = {
                            attempt: active,
                            confirmation: decision,
                            resolve,
                            activate: () => {
                                if (current(active)) {
                                    setPrompt(next);
                                } else {
                                    removeDecision(next);
                                    resolve(undefined);
                                }
                            },
                        };
                        promptRef.current = next;
                        decisions.push(next);
                        if (decisions.length === 1) next.activate();
                    });
                    if (!current(active)) return;
                    if (!choice) {
                        cancel();
                        return;
                    }
                    active.choice = choice;
                    continue;
                }
                if (!attachment(response.data, active.requestUID, cardUID)) throw new Error("Invalid upload result");
                attemptRef.current = undefined;
                return { ...response.data, created_at: new Date(response.data.created_at), updated_at: new Date(response.data.updated_at) };
            }
        } catch (error) {
            if (!current(active)) return;
            // Retain the exact UUID and decision only for an ambiguous transport retry.
            // All error payloads remain private, including unrecognized conflicts.
            if (isAxiosError(error) && (!error.response || error.response.status >= 500)) {
                throw new Error(t("card.Attachment upload failed. Retry the same file or cancel."));
            }
            cancel();
            throw new Error(t("card.Attachment upload failed. Retry the same file or cancel."));
        }
    }
    const decisionDialog = (
        <Dialog.Root open={!!visiblePrompt} onOpenChange={(open) => !open && decide(undefined)}>
            {visiblePrompt && (
                <Dialog.Content onClick={(event) => event.stopPropagation()} onPointerDown={(event) => event.stopPropagation()}>
                    <Dialog.Header>
                        <Dialog.Title>{t("card.Duplicate attachment")}</Dialog.Title>
                        <Dialog.Description>{t("card.Choose how to upload this file", { name: visiblePrompt.attempt.file.name })}</Dialog.Description>
                    </Dialog.Header>
                    <ul className="my-4 space-y-3">
                        {visiblePrompt.confirmation.conflicts.map((conflict) => {
                            const sameNames = visiblePrompt.confirmation.conflicts.filter((item) => item.name === conflict.name);
                            const targetName = sameNames.length > 1 ? `${conflict.name} (${conflict.uid})` : conflict.name;
                            return (
                                <li key={conflict.uid} className="space-y-1 break-words">
                                    <div>{targetName}</div>
                                    <div className="text-sm">
                                        {conflict.same_name && <div>{t("card.Same file name")}</div>}
                                        {conflict.same_content && <div>{t("card.Same file content")}</div>}
                                    </div>
                                    {conflict.can_replace && (
                                        <Button variant="outline" onClick={() => decide({ action: "replace", replacementUID: conflict.uid })}>
                                            {t("card.Replace this attachment", { name: targetName })}
                                        </Button>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                    <Dialog.Footer className="gap-2">
                        <Button variant="secondary" onClick={() => decide(undefined)}>
                            {t("common.Cancel")}
                        </Button>
                        <Button onClick={() => decide({ action: "add" })}>{t("card.Add separate attachment")}</Button>
                    </Dialog.Footer>
                </Dialog.Content>
            )}
        </Dialog.Root>
    );
    return { upload, decisionDialog };
}
