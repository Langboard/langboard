import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";

export interface TransferApp { appKey: string; projectUID: string; userUID: string; enabled?: boolean }
export interface TransferSource { cardUID: string; attachmentUID?: string }
interface Intent { card_revision: number; resource_uid: string | null; attachment_uid: string | null; attachment_revision: string | null; content_sha256: string | null }
interface Review { title: string; attachment_name: string | null; intent: Intent; targets: { connection_uid: string; resource_uid: string; resource_type: string; external_resource_id: string; authority: Record<string, unknown> }[]; next_cursor: string | null }
interface Receipt { found?: boolean; transfer_uid: string; receipt: { revision: number; retry_revision: number; acknowledgment: string | null; provider_outcome: string | null }; delivery: { state: string; attempt_count: number; last_error: string | null; history: { attempt: number; outcome: string }[] } }
interface Pending { clientRequestID: string; source: TransferSource; connectionUID: string; intent: Intent; expectedAuthority: Record<string, unknown> }
export const transferStorageKey = (app: TransferApp) => `app-transfer:${app.userUID}:${app.projectUID}:${app.appKey}`;

/** Keep the original request across transport errors and reloads; GET reconciles it. */
export default function AppTransferDialog({ app, source, onClose }: { app: TransferApp; source?: TransferSource; onClose: () => void }) {
    const [t] = useTranslation();
    const [review, setReview] = useState<Review>();
    const [receipt, setReceipt] = useState<Receipt>();
    const [target, setTarget] = useState("");
    const [error, setError] = useState(false);
    const [busy, setBusy] = useState(false);
    const [pending, setPending] = useState<Pending | undefined>(() => {
        try { return JSON.parse(sessionStorage.getItem(transferStorageKey(app)) ?? "null") ?? undefined; } catch { return undefined; }
    });
    const session = useRef(useAuthStore.getState().getSessionVersion()).current;
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const version = useAuthStore((state) => state.getSessionVersion());
    const current = () => useAuthStore.getState().currentUser?.uid === app.userUID && useAuthStore.getState().getSessionVersion() === session;
    const live = useRef(true);
    const inFlight = useRef(false);
    const config = { env: { interceptToast: true } as never };
    const path = `/board/${app.projectUID}/app-transfers/by-request/`;
    const run = async (action: () => Promise<void>) => {
        if (!current() || inFlight.current) return;
        inFlight.current = true;
        setBusy(true); setError(false);
        try { await action(); } catch { if (live.current && current()) setError(true); }
        finally { inFlight.current = false; if (live.current && current()) setBusy(false); }
    };
    const reconcile = async (request: Pending) => {
        const result = (await api.get<Receipt>(path + request.clientRequestID, config)).data;
        if (live.current && current()) setReceipt(result);
    };
    const loadReview = async (after?: string) => {
        if (!source) return;
        const result = (await api.get<Review>(`/board/${app.projectUID}/card/${source.cardUID}/apps/${encodeURIComponent(app.appKey)}/transfer-review`,
            { ...config, params: { attachment_uid: source.attachmentUID, after } })).data;
        if (live.current && current()) { setReview(result); setTarget(""); }
    };
    useEffect(() => {
        live.current = true;
        void run(async () => { if (pending) await reconcile(pending); else await loadReview(); });
        return () => { live.current = false; };
    }, []);
    useEffect(() => { if (actor !== app.userUID || version !== session) onClose(); }, [actor, version, session, app.userUID, onClose]);
    const send = async () => {
        if (!source || !review) return;
        const selected = review.targets.find((row) => row.resource_uid === target);
        if (!selected) return;
        const request: Pending = { clientRequestID: crypto.randomUUID(), source, connectionUID: selected.connection_uid,
            expectedAuthority: selected.authority, intent: { ...review.intent, resource_uid: selected.resource_uid } };
        // Persistence must succeed before the first POST; failures cannot lose its identity.
        sessionStorage.setItem(transferStorageKey(app), JSON.stringify(request));
        setPending(request);
        const result = (await api.post<Receipt>(`/board/${app.projectUID}/card/${source.cardUID}/apps/${encodeURIComponent(app.appKey)}/transfers`,
            { client_request_id: request.clientRequestID, connection_uid: request.connectionUID, intent: request.intent, expected_authority: request.expectedAuthority }, config)).data;
        if (live.current && current()) setReceipt(result);
    };
    return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
        <Dialog.Content withCloseButton={!busy} disableOverlayClick={busy}>
            <Dialog.Header><Dialog.Title>{t("appStore.Transfer to app")}</Dialog.Title>
                <Dialog.Description>{t("appStore.Transfer review help")}</Dialog.Description></Dialog.Header>
            {busy && <p role="status">{t("common.Loading...")}</p>}
            {error && <p role="alert">{t("appStore.Transfer refresh required")}</p>}
            {!pending && review && <>
                <p>{review.title}</p>{review.attachment_name && <p>{review.attachment_name}</p>}
                <select aria-label={t("appStore.Transfer destination")} value={target} disabled={busy} onChange={(event) => setTarget(event.target.value)}>
                    <option value="">{t("appStore.Transfer destination")}</option>
                    {review.targets.map((row) => <option key={row.resource_uid} value={row.resource_uid}>{row.resource_type}: {row.external_resource_id}</option>)}
                </select>
                {review.next_cursor && <Button disabled={busy} onClick={() => void run(() => loadReview(review.next_cursor!))}>{t("common.More")}</Button>}
                <Button disabled={busy || !target} onClick={() => void run(send)}>{t("appStore.Send transfer")}</Button>
            </>}
            {!pending && !source && <p>{t("appStore.Drop a card or attachment")}</p>}
            {pending && <>
                <p>{t("appStore.Transfer request")}: {pending.clientRequestID}</p>
                {(!receipt || receipt.found === false) && <p role="status">{t("appStore.Transfer outcome unknown")}</p>}
                {receipt && receipt.found !== false && <>
                    <p>{t("appStore.Transport status")}: {t(`appStore.transferStates.${receipt.delivery.state}`)}</p>
                    <p>{t("appStore.App acknowledgment")}: {t(`appStore.transferStates.${receipt.receipt.acknowledgment ?? "unconfirmed"}`)}</p>
                    <p>{t("appStore.Provider outcome")}: {t(`appStore.transferStates.${receipt.receipt.provider_outcome ?? "unconfirmed"}`)}</p>
                    <ol>{receipt.delivery.history.map((entry, index) => <li key={index}>{entry.attempt}: {t(`appStore.transferStates.${entry.outcome}`)}</li>)}</ol>
                    {receipt.delivery.state === "failed" && !receipt.receipt.acknowledgment && <Button disabled={busy} onClick={() => void run(async () => {
                        const result = (await api.post<Receipt>(path + pending.clientRequestID + "/retry", { expected_retry_revision: receipt.receipt.retry_revision }, config)).data;
                        if (live.current && current()) setReceipt(result);
                    })}>{t("appStore.Retry transfer delivery")}</Button>}
                </>}
                {receipt?.found === false && <Button disabled={busy} onClick={() => void run(async () => {
                    const result = (await api.post<Receipt>(`/board/${app.projectUID}/card/${pending.source.cardUID}/apps/${encodeURIComponent(app.appKey)}/transfers`,
                        { client_request_id: pending.clientRequestID, connection_uid: pending.connectionUID, intent: pending.intent, expected_authority: pending.expectedAuthority }, config)).data;
                    if (live.current && current()) setReceipt(result);
                })}>{t("appStore.Retry same transfer request")}</Button>}
                <Button disabled={busy} onClick={() => void run(() => reconcile(pending))}>{t("appStore.Refresh transfer status")}</Button>
                {receipt && receipt.found !== false && receipt.delivery.state !== "pending" && receipt.delivery.state !== "delivering" && <Button disabled={busy} onClick={() => {
                    sessionStorage.removeItem(transferStorageKey(app)); setPending(undefined); setReceipt(undefined); onClose();
                }}>{t("appStore.Close transfer receipt")}</Button>}
            </>}
        </Dialog.Content>
    </Dialog.Root>;
}
