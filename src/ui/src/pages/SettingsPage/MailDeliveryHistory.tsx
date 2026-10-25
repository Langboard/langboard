import { ROUTES } from "@/core/routing/constants";
import Button from "@/components/base/Button";
import Card from "@/components/base/Card";
import Input from "@/components/base/Input";
import { InputVariants } from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const statuses = ["pending", "preparing", "sending", "sent", "failed", "uncertain", "suppressed", "confirmed_sent", "closed"];
const evidence = ["not_attempted", "claimed", "smtp_accepted", "operator_confirmed", "smtp_rejected", "unknown"];
interface IDelivery {
    resources?: { kind: "board" | "card"; project_uid: string; card_uid?: string }[];
    key: string;
    source: "notification" | "project_activity" | "smtp_attempt";
    record_kind?: "smtp_attempt" | "outbox_latest";
    started_at?: string | null;
    completed_at?: string | null;
    subject?: string | null;
    outbox_key?: string | null;
    recorded_attempt_count?: number | null;
    uid: string;
    created_at: string;
    claimed_at: string | null;
    sent_at: string | null;
    status: string;
    recipient_masked: string;
    trigger: string;
    template: string | null;
    safe_error_code: string | null;
    attempt_evidence: string;
}
interface IHistory {
    items: IDelivery[];
    next_cursor: string | null;
}

export default function MailDeliveryHistory({ isCurrent, onPermissionDenied }: { isCurrent: () => boolean; onPermissionDenied: () => void }) {
    const [t] = useTranslation();
    const [view, setView] = useState("attempts");
    const [status, setStatus] = useState("");
    const [from, setFrom] = useState("");
    const [to, setTo] = useState("");
    const [items, setItems] = useState<IDelivery[] | null>(null);
    const [cursor, setCursor] = useState<string | null>(null);
    const [detail, setDetail] = useState<IDelivery | null>(null);
    const [busy, setBusy] = useState(false);
    const [failure, setFailure] = useState<string | null>(null);
    const pending = useRef(false);
    const active = useRef(true);
    const applied = useRef<Record<string, string | number>>({ limit: 25 });
    useEffect(() => {
        active.current = true;
        return () => {
            active.current = false;
        };
    }, []);
    const current = () => active.current && isCurrent();
    const request = async (kind: "filter" | "more" | "detail", item?: IDelivery) => {
        if (!current() || pending.current) return;
        if (kind === "filter" && from && to && from >= to) {
            setFailure("dateRangeError");
            return;
        }
        pending.current = true;
        setBusy(true);
        setFailure(null);
        setDetail(null);
        if (kind === "filter") {
            applied.current = {
                limit: 25,
                view,
                ...(status ? { status } : {}),
                ...(from ? { created_from: `${from}T00:00:00.000Z` } : {}),
                ...(to ? { created_to: `${to}T00:00:00.000Z` } : {}),
            };
            setItems(null);
            setCursor(null);
        }
        try {
            const options = { validateStatus: () => true, env: { interceptToast: true } as never };
            const response =
                kind === "detail" && item
                    ? await api.get<IDelivery>(
                          `/settings/mail/deliveries/${encodeURIComponent(item.source)}/${encodeURIComponent(item.uid)}`,
                          options
                      )
                    : await api.get<IHistory>("/settings/mail/deliveries", {
                          ...options,
                          params: { ...applied.current, ...(kind === "more" && cursor ? { cursor } : {}) },
                      });
            if (!current()) return;
            if (response.status === 401 || response.status === 403) {
                onPermissionDenied();
                return;
            }
            if (response.status !== 200) {
                setFailure(response.status === 404 ? "detailMissing" : "historyError");
                return;
            }
            if (kind === "detail") setDetail(response.data as IDelivery);
            else {
                const page = response.data as IHistory;
                setItems((previous) =>
                    kind === "more"
                        ? [...(previous ?? []), ...page.items.filter((row) => !previous?.some((old) => old.key === row.key))].slice(0, 250)
                        : page.items.slice(0, 25)
                );
                setCursor(page.next_cursor);
            }
        } catch {
            if (current()) setFailure("historyError");
        } finally {
            if (current()) {
                pending.current = false;
                setBusy(false);
            }
        }
    };
    const errorCode = detail?.safe_error_code;
    const safeError =
        errorCode &&
        [
            "worker_outcome_unknown",
            "error_detail_unavailable",
            "smtp_rejected",
            "authentication_failed",
            "connection_failed",
            "tls_failed",
            "timeout",
            "transport_error",
        ].includes(errorCode)
            ? errorCode
            : "unknownError";
    const attempt = detail && evidence.includes(detail.attempt_evidence) ? detail.attempt_evidence : "unknown";
    const actualAttempt = detail?.source === "smtp_attempt";
    const stateLabel = (value: string) => t(`settings.mail.status_${statuses.includes(value) ? value : "unknown"}`);
    return (
        <Card.Root>
            <Card.Header>
                <Card.Title>{t("settings.mail.history")}</Card.Title>
            </Card.Header>
            <Card.Content className="space-y-4">
                <p>{t("settings.mail.historyCoverage")}</p>
                <p className="text-sm">{t("settings.mail.historyUnknown")}</p>
                <form
                    className="space-y-3"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void request("filter");
                    }}
                >
                    <fieldset disabled={busy} className="grid min-w-0 gap-3 sm:grid-cols-3">
                        <label className="block min-w-0 space-y-1">
                            <span>{t("settings.mail.historyView")}</span>
                            <select className={InputVariants()} value={view} onChange={(event) => setView(event.target.value)}>
                                <option value="attempts">{t("settings.mail.attemptView")}</option>
                                <option value="legacy">{t("settings.mail.legacyView")}</option>
                            </select>
                        </label>
                        <label className="block min-w-0 space-y-1">
                            <span>{t("settings.mail.statusFilter")}</span>
                            <select className={InputVariants()} value={status} onChange={(event) => setStatus(event.target.value)}>
                                <option value="">{t("settings.mail.allStatuses")}</option>
                                {statuses.map((value) => (
                                    <option key={value} value={value}>
                                        {stateLabel(value)}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label className="block min-w-0 space-y-1">
                            <span>{t("settings.mail.createdFrom")}</span>
                            <Input type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
                        </label>
                        <label className="block min-w-0 space-y-1">
                            <span>{t("settings.mail.createdTo")}</span>
                            <Input type="date" value={to} onChange={(event) => setTo(event.target.value)} />
                        </label>
                    </fieldset>
                    <p className="text-sm">{t("settings.mail.dateHelp")}</p>
                    <Button type="submit" disabled={busy}>
                        {t("settings.mail.loadHistory")}
                    </Button>
                </form>
                {busy && <p role="status">{t("common.Loading...")}</p>}
                {failure && <p role="alert">{t(`settings.mail.${failure}`)}</p>}
                {items?.length === 0 && <p role="status">{t("settings.mail.noHistory")}</p>}
                {items && (
                    <ul className="space-y-3" aria-label={t("settings.mail.history")}>
                        {items.map((item) => (
                            <li key={item.key} className="space-y-2 break-words rounded-md border p-3">
                                <p>
                                    {item.recipient_masked} · {stateLabel(item.status)}
                                </p>
                                <p>
                                    {item.started_at || item.created_at} · {t(`settings.mail.source_${item.source}`)}
                                </p>
                                <Button disabled={busy} onClick={() => void request("detail", item)}>
                                    {t("settings.mail.viewDelivery", { id: item.uid })}
                                </Button>
                            </li>
                        ))}
                    </ul>
                )}
                {cursor && items && items.length < 250 && (
                    <Button disabled={busy} onClick={() => void request("more")}>
                        {t("settings.mail.loadMore")}
                    </Button>
                )}
                {items && items.length >= 250 && <p>{t("settings.mail.historyLimit")}</p>}
                <div aria-live="polite">
                    {detail && (
                        <section aria-label={t("settings.mail.deliveryDetail")} className="space-y-3 break-words rounded-md border p-3">
                            <h3 className="font-semibold">{t("settings.mail.deliveryDetail")}</h3>
                            <dl className="space-y-2">
                                {[
                                    ["recipient", detail.recipient_masked],
                                    ["statusFilter", stateLabel(detail.status)],
                                    ["source", t(`settings.mail.source_${detail.source}`)],
                                    ["createdAt", detail.created_at],
                                    ...(actualAttempt
                                        ? [
                                              ["startedAt", detail.started_at || t("settings.mail.notRecorded")],
                                              ["completedAt", detail.completed_at || t("settings.mail.notRecorded")],
                                              ["subject", detail.subject || t("settings.mail.notRecorded")],
                                              ["outboxKey", detail.outbox_key || t("settings.mail.notRecorded")],
                                          ]
                                        : [
                                              ["claimedAt", detail.claimed_at || t("settings.mail.notRecorded")],
                                              ["sentAt", detail.sent_at || t("settings.mail.notRecorded")],
                                          ]),
                                    ["trigger", detail.trigger],
                                    ["template", detail.template || t("settings.mail.notRecorded")],
                                    ["attemptEvidence", t(`settings.mail.evidence_${attempt}`)],
                                    ["recordedAttemptCount", detail.recorded_attempt_count?.toString() ?? t("settings.mail.notRecorded")],
                                    [
                                        "retryProvenance",
                                        t(
                                            `settings.mail.${
                                                detail.recorded_attempt_count == null
                                                    ? "retryUnknown"
                                                    : detail.recorded_attempt_count === 0
                                                      ? "noRecordedAttempts"
                                                      : detail.recorded_attempt_count === 1
                                                        ? "oneRecordedAttempt"
                                                        : "multipleRecordedAttempts"
                                            }`
                                        ),
                                    ],
                                    [
                                        "safeError",
                                        detail.safe_error_code
                                            ? t(`settings.mail.${safeError === "timeout" ? "attemptTimeout" : safeError}`)
                                            : t("settings.mail.notRecorded"),
                                    ],
                                ].map(([label, value]) => (
                                    <div key={label}>
                                        <dt className="font-medium">{t(`settings.mail.${label}`)}</dt>
                                        <dd>{value}</dd>
                                    </div>
                                ))}
                            </dl>
                            {!!detail.resources?.length && (
                                <nav aria-label={t("settings.mail.relatedResources")}>
                                    {detail.resources.map((resource) => (
                                        <a
                                            key={`${resource.kind}:${resource.project_uid}:${resource.card_uid ?? ""}`}
                                            className="underline"
                                            href={
                                                resource.kind === "card" && resource.card_uid
                                                    ? ROUTES.BOARD.CARD(
                                                          encodeURIComponent(resource.project_uid),
                                                          encodeURIComponent(resource.card_uid)
                                                      )
                                                    : ROUTES.BOARD.MAIN(encodeURIComponent(resource.project_uid))
                                            }
                                        >
                                            {t(`settings.mail.${resource.kind === "card" ? "relatedCard" : "relatedBoard"}`)}
                                        </a>
                                    ))}
                                </nav>
                            )}
                            <p>{t("settings.mail.historyUnknown")}</p>
                            <p>{t("settings.mail.inboxUnverified")}</p>
                        </section>
                    )}
                </div>
            </Card.Content>
        </Card.Root>
    );
}
