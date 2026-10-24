import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/core/helpers/Api";
import { formatDateTime } from "@/core/utils/LocaleFormat";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import SecretCopyForm from "./SecretCopyForm";

interface HistoryItem {
    uid: string;
    created_at: string;
    actor_uid: string | null;
    actor_kind?: "user" | "workload";
    principal_id?: string | null;
    action: string;
    revision_before: number | null;
    revision_after: number;
    source_kind: string;
    source_link?: { kind: "card" | "wiki"; href: string };
    request_id?: string | null;
    reason_code?: string | null;
}
type ReferenceState = "active" | "revoked";

async function loadHistory(url: string) {
    const [history, metadata] = await Promise.all([api.get(url), api.get(url.replace(/\/history$/, ""))]);
    const state = metadata.data.reference?.state;
    if (state !== "active" && state !== "revoked") throw new Error("Invalid reference state");
    return { items: history.data.items as HistoryItem[], cursor: history.data.next_cursor as string | null, state: state as ReferenceState };
}

export default function SecretHistoryPage() {
    const { referenceUID } = useParams();
    const [t, i18n] = useTranslation();
    const [items, setItems] = useState<HistoryItem[]>([]);
    const [referenceState, setReferenceState] = useState<{ state: ReferenceState; generation: object }>();
    const [cursor, setCursor] = useState<string | null>(null);
    const [copyReference, setCopyReference] = useState<string>();
    const copyOpen = copyReference === referenceUID;
    const [busy, setBusy] = useState(true);
    const [failed, setFailed] = useState(false);
    const currentRequest = useRef<object | null>(null);
    const url = `/secret-references/${referenceUID}/history`;
    const requestGeneration = useMemo(() => ({}), [url]);
    currentRequest.current = requestGeneration;
    useEffect(() => {
        currentRequest.current = requestGeneration;
        let active = true;
        setCopyReference(undefined);
        setReferenceState(undefined);
        setItems([]);
        setBusy(true);
        setFailed(false);
        setCursor(null);
        loadHistory(url)
            .then((data) => {
                if (active) {
                    setItems(data.items);
                    setCursor(data.cursor);
                    setReferenceState({ state: data.state, generation: requestGeneration });
                }
            })
            .catch(() => {
                if (active) setFailed(true);
            })
            .finally(() => {
                if (active) setBusy(false);
            });
        return () => {
            active = false;
            if (currentRequest.current === requestGeneration) currentRequest.current = null;
        };
    }, [url, requestGeneration]);
    return (
        <main className="mx-auto w-full max-w-2xl px-4 py-10 sm:py-16">
            <section className="card card-border rounded-xl border bg-background">
                <div className="card-body flex flex-col gap-4 p-5 sm:p-6">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h1 className="card-title text-lg font-semibold">{t("myAccount.secretHistory.title")}</h1>
                        <Button variant="ghost" size="sm" disabled={busy || failed || copyOpen} onClick={() => setCopyReference(referenceUID)}>
                            <IconComponent icon="copy" size="4" />
                            {t("myAccount.secretCopy.title")}
                        </Button>
                    </div>
                    <p className="text-sm text-muted-foreground">{t("myAccount.secretHistory.help")}</p>
                    {!failed && referenceState?.generation === requestGeneration && (
                        <p className="flex flex-wrap items-center gap-2 text-sm">
                            <span>{t("myAccount.secretHistory.currentState")}</span>
                            <span className="badge badge-outline">{t(`myAccount.secretHistory.states.${referenceState.state}`)}</span>
                        </p>
                    )}
                    {copyOpen && referenceUID && !failed && (
                        <SecretCopyForm
                            key={referenceUID}
                            referenceUID={referenceUID}
                            onClose={() => setCopyReference(undefined)}
                            onCopied={async () => {
                                try {
                                    const data = await loadHistory(url);
                                    if (currentRequest.current !== requestGeneration) return;
                                    setItems(data.items);
                                    setCursor(data.cursor);
                                    setReferenceState({ state: data.state, generation: requestGeneration });
                                } catch {
                                    if (currentRequest.current === requestGeneration) {
                                        setItems([]);
                                        setReferenceState(undefined);
                                        setCursor(null);
                                        setFailed(true);
                                    }
                                }
                            }}
                        />
                    )}
                    {failed ? (
                        <p role="alert">{t("myAccount.secretHistory.failed")}</p>
                    ) : (
                        <>
                            <ol className="flex flex-col divide-y">
                                {items.map((item) => (
                                    <li key={item.uid} className="flex flex-col gap-1 py-3 text-sm">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <strong>{t(`myAccount.secretHistory.actions.${item.action}`, { defaultValue: item.action })}</strong>
                                            <time className="text-xs text-muted-foreground" dateTime={item.created_at}>
                                                {formatDateTime(new Date(item.created_at), i18n.language)}
                                            </time>
                                        </div>
                                        <p className="break-all text-xs text-muted-foreground">
                                            {item.actor_kind === "workload"
                                                ? t("myAccount.secretHistory.keywordWorkload")
                                                : `${t("myAccount.secretHistory.actor")} ${item.actor_uid ?? ""}`}{" "}
                                            · {item.source_kind}
                                            {" · "}
                                            {item.revision_before === null
                                                ? `v${item.revision_after}`
                                                : `v${item.revision_before} → v${item.revision_after}`}
                                        </p>
                                        {(item.source_link?.kind === "card"
                                            ? /^\/board\/[A-Za-z0-9]{1,11}\/[A-Za-z0-9]{1,11}$/.test(item.source_link.href)
                                            : item.source_link?.kind === "wiki" &&
                                              /^\/board\/[A-Za-z0-9]{1,11}\/wiki\/[A-Za-z0-9]{1,11}$/.test(item.source_link.href)) && (
                                            <Link className="link text-xs text-primary" to={item.source_link!.href}>
                                                {t(
                                                    item.source_link!.kind === "wiki"
                                                        ? "myAccount.secretHistory.openSourceWiki"
                                                        : "myAccount.secretHistory.openSource"
                                                )}
                                            </Link>
                                        )}
                                        {item.reason_code && (
                                            <p className="text-xs text-muted-foreground" title={item.request_id ?? undefined}>
                                                {t(`myAccount.secretHistory.reasons.${item.reason_code}`, { defaultValue: item.reason_code })}
                                            </p>
                                        )}
                                    </li>
                                ))}
                            </ol>
                            {!busy && !items.length && <p>{t("myAccount.secretHistory.empty")}</p>}
                            {busy && <p role="status">{t("common.Loading...")}</p>}
                            {cursor && (
                                <Button
                                    disabled={busy}
                                    onClick={async () => {
                                        setBusy(true);
                                        try {
                                            const { data } = await api.get(url, { params: { cursor } });
                                            if (currentRequest.current !== requestGeneration) return;
                                            setItems((previous) => [...previous, ...data.items]);
                                            setCursor(data.next_cursor);
                                        } catch {
                                            if (currentRequest.current !== requestGeneration) return;
                                            setItems([]);
                                            setReferenceState(undefined);
                                            setCursor(null);
                                            setFailed(true);
                                        } finally {
                                            if (currentRequest.current === requestGeneration) setBusy(false);
                                        }
                                    }}
                                >
                                    {t("myAccount.secretHistory.older")}
                                </Button>
                            )}
                        </>
                    )}
                </div>
            </section>
        </main>
    );
}
