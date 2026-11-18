import SignalPersonalInbox from "./SignalPersonalInbox";
import useAuthStore from "@/core/stores/AuthStore";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { formatDateTime } from "@/core/utils/LocaleFormat";

interface Receipt {
    dispatch_uid: string;
    created_at: string;
    expires_at: string;
    status: string;
    attempt_count: number;
}
interface Detail extends Receipt {
    content_available: boolean;
    notification: { title?: string; body?: string } | null;
}
type Page = { items: Receipt[]; next_cursor: string | null };
const root = "/settings/apps/notification-outbox";
const statuses = [
    "pending",
    "delivering",
    "transport_delivered",
    "adapter_accepted",
    "confirmed",
    "unknown",
    "rejected",
    "blocked",
    "expired",
    "failed",
];

export default function SignalOutboxPanel({ projectUID, onPermissionDenied }: { projectUID: string; onPermissionDenied: () => void }) {
    const userUID = useAuthStore((state) => state.currentUser?.uid);
    const [t, i18n] = useTranslation();
    const text = (key: string) => t(`card.outbox.${key}`);
    const [showSetup, setShowSetup] = useState(false);
    const [items, setItems] = useState<Receipt[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [detail, setDetail] = useState<Detail | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState(false);
    const controller = useRef<AbortController | null>(null);
    const generation = useRef(0);
    const clear = () => {
        setItems([]);
        setCursor(null);
        setDetail(null);
    };
    const run = async (action: (signal: AbortSignal) => Promise<void>) => {
        controller.current?.abort();
        const request = new AbortController();
        controller.current = request;
        const current = ++generation.current;
        setPending(true);
        setError(false);
        try {
            await action(request.signal);
        } catch (error) {
            if (!request.signal.aborted && current === generation.current) {
                clear();
                const status = (error as { response?: { status?: number } })?.response?.status;
                if (status === 401 || status === 403) onPermissionDenied();
                else setError(true);
            }
        } finally {
            if (current === generation.current) setPending(false);
        }
    };
    const load = (after?: string) => {
        setDetail(null);
        void run(async (signal) => {
            const response = await api.get<Page>(root, { signal, params: after ? { after } : undefined });
            if (signal.aborted) return;
            setItems((previous) =>
                after
                    ? [...previous, ...response.data.items.filter((item) => !previous.some((old) => old.dispatch_uid === item.dispatch_uid))]
                    : response.data.items
            );
            setCursor(response.data.next_cursor);
        });
    };
    useEffect(() => {
        if (document.visibilityState === "visible") load();
        const hidden = () => {
            if (document.visibilityState !== "visible") {
                generation.current++;
                controller.current?.abort();
                clear();
                setPending(false);
            }
        };
        document.addEventListener("visibilitychange", hidden);
        return () => {
            generation.current++;
            controller.current?.abort();
            document.removeEventListener("visibilitychange", hidden);
        };
    }, []);
    const select = (uid: string) => {
        setDetail(null);
        void run(async (signal) => {
            const response = await api.get<Detail>(`${root}/${encodeURIComponent(uid)}`, { signal });
            if (!signal.aborted) setDetail(response.data);
        });
    };
    return (
        <section className="h-full overflow-y-auto p-3" aria-label={text("title")}>
            {userUID && (
                <div className="mb-3">
                    <Button size="sm" variant="outline" aria-expanded={showSetup} onClick={() => setShowSetup((value) => !value)}>
                        {t(showSetup ? "card.personalInbox.closeSetup" : "card.personalInbox.title")}
                    </Button>
                    {showSetup && <SignalPersonalInbox projectUID={projectUID} userUID={userUID} />}
                </div>
            )}
            <h2 className="mb-3 flex items-center justify-between gap-2 text-sm font-semibold">
                {text("title")}
                <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={() => load()}>
                    {text("refresh")}
                </Button>
            </h2>
            <p className="mb-3 text-xs text-muted-foreground">{text("hint")}</p>
            {pending && <p role="status">{text("loading")}</p>}
            {error && <p role="alert">{text("error")}</p>}
            {!pending && !error && !items.length && <p className="text-sm text-muted-foreground">{text("empty")}</p>}
            <div className="space-y-2">
                {items.map((item) => (
                    <button
                        type="button"
                        key={item.dispatch_uid}
                        disabled={pending}
                        onClick={() => select(item.dispatch_uid)}
                        className="flex w-full flex-col gap-1 rounded-md border border-border p-2 text-left hover:bg-accent"
                    >
                        <span className="badge badge-outline">{text(statuses.includes(item.status) ? item.status : "unavailable")}</span>
                        <span className="text-xs">{formatDateTime(new Date(item.created_at), i18n.language)}</span>
                        <span className="text-xs text-muted-foreground">
                            {text("attempts")} {item.attempt_count}
                        </span>
                    </button>
                ))}
            </div>
            {cursor && (
                <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={() => load(cursor)}>
                    {text("more")}
                </Button>
            )}
            {detail && (
                <div className="mt-3 space-y-2 rounded-md border border-border p-3 text-sm">
                    <p>{text(statuses.includes(detail.status) ? detail.status : "unavailable")}</p>
                    {detail.content_available && detail.notification ? (
                        <>
                            <p className="break-words font-semibold">{detail.notification.title}</p>
                            <p className="whitespace-pre-wrap break-words">{detail.notification.body}</p>
                        </>
                    ) : (
                        <p>{text("contentUnavailable")}</p>
                    )}
                </div>
            )}
        </section>
    );
}
