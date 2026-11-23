import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import Textarea from "@/components/base/Textarea";
import { api } from "@/core/helpers/Api";

type Destination = { destination_uid: string; kind: string; state: string; revision: number; display_name: string };
type Subscription = { subscription_uid: string; destination_uid: string; scope: string; state: string; generation: number; event_kinds: string[] };
type Receipt = { dispatch_uid: string; event_uid: string; subscription_uid: string; status: string };
type Operation = { context: string; subscription_uid: string; request_id: string; event_uid?: string };
type Page<T> = { items: T[]; next_cursor: string | null };
const options = { env: { interceptToast: true } as never };
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

// Retain only reconciliation metadata across selection/visibility changes while mounted.
export default function SignalTestNotification({
    context,
    connectionUID,
    destination,
    subscription,
    enabled,
    onReceipt,
}: {
    context: string;
    connectionUID: string;
    destination?: Destination;
    subscription?: Subscription;
    enabled: boolean;
    onReceipt?: () => void;
}) {
    const [t] = useTranslation();
    const text = (key: string) => t(`card.testNotification.${key}`);
    const selection = JSON.stringify([
        context,
        connectionUID,
        destination?.destination_uid,
        destination?.revision,
        subscription?.subscription_uid,
        subscription?.generation,
        subscription?.event_kinds,
    ]);
    const eligible =
        enabled &&
        destination?.kind === "personal" &&
        destination.state === "verified" &&
        subscription?.scope === "personal" &&
        subscription.state === "enabled" &&
        subscription.destination_uid === destination.destination_uid &&
        subscription.event_kinds.includes("test");
    const [title, setTitle] = useState("");
    const [body, setBody] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [unresolved, setUnresolved] = useState(false);
    const [receipt, setReceipt] = useState<Receipt | null>(null);
    const [error, setError] = useState(false);
    const [denied, setDenied] = useState(false);
    const [visible, setVisible] = useState(document.visibilityState === "visible");
    const controller = useRef<AbortController | null>(null);
    const epoch = useRef(0);
    const writing = useRef(false);
    const operation = useRef<Operation | null>(null);
    const receiptCallback = useRef(onReceipt);
    receiptCallback.current = onReceipt;
    useLayoutEffect(() => {
        const clear = () => {
            epoch.current++;
            controller.current?.abort();
            writing.current = false;
            setTitle("");
            setBody("");
            setConfirmed(false);
            setReceipt(null);
            setError(false);
            setDenied(false);
            setBusy(false);
            setUnresolved(!!operation.current);
        };
        clear();
        const hide = () => {
            clear();
            setVisible(false);
        };
        const visibility = () => {
            if (document.visibilityState !== "visible") hide();
            else setVisible(true);
        };
        window.addEventListener("pagehide", hide);
        document.addEventListener("visibilitychange", visibility);
        return () => {
            epoch.current++;
            controller.current?.abort();
            window.removeEventListener("pagehide", hide);
            document.removeEventListener("visibilitychange", visibility);
        };
    }, [selection, eligible]);
    const run = async (action: (signal: AbortSignal, alive: () => boolean) => Promise<void>) => {
        if (writing.current || !visible) return;
        writing.current = true;
        setBusy(true);
        setError(false);
        const current = epoch.current;
        const ctrl = new AbortController();
        controller.current = ctrl;
        const alive = () => !ctrl.signal.aborted && current === epoch.current;
        try {
            await action(ctrl.signal, alive);
        } catch (failure) {
            if (alive()) {
                const status = (failure as { response?: { status?: number } }).response?.status;
                if (status === 401 || status === 403) setDenied(true);
                setTitle("");
                setBody("");
                setConfirmed(false);
                setReceipt(null);
                setError(true);
            }
        } finally {
            if (alive()) {
                writing.current = false;
                setBusy(false);
            }
        }
    };
    const reconcile = async (signal: AbortSignal, alive: () => boolean) => {
        const pending = operation.current;
        if (!pending || pending.context !== context) return;
        const response = await api.get<Page<Receipt> & { lookup?: { subscription_uid: string; request_id: string } }>(
            "/settings/apps/notification-outbox",
            { ...options, signal, params: { subscription_uid: pending.subscription_uid, request_id: pending.request_id } }
        );
        if (!alive()) return;
        const page = response.data;
        const match = page.items?.[0];
        if (
            page.lookup?.subscription_uid !== pending.subscription_uid ||
            page.lookup?.request_id !== pending.request_id ||
            page.next_cursor !== null ||
            page.items?.length !== 1 ||
            !match?.dispatch_uid ||
            !match.event_uid ||
            match.subscription_uid !== pending.subscription_uid ||
            (pending.event_uid && match.event_uid !== pending.event_uid)
        )
            return;
        operation.current = null;
        setUnresolved(false);
        setReceipt(match);
        receiptCallback.current?.();
    };
    const send = () => {
        if (!eligible || !confirmed || !title.trim() || !body.trim() || title.length > 120 || body.length > 2000 || operation.current) return;
        void run(async (signal, alive) => {
            // Refresh the selected existing authority; never create or expand consent here.
            const root = `/settings/apps/connections/${encodeURIComponent(connectionUID)}/notification-settings`;
            const find = async <T extends Destination | Subscription>(section: string, matches: (item: T) => boolean) => {
                let after: string | null = null;
                const cursors = new Set<string>();
                do {
                    const page: Page<T> = (await api.get<Page<T>>(root, { ...options, signal, params: { section, ...(after ? { after } : {}) } }))
                        .data;
                    const item = page.items.find(matches);
                    if (item) return item;
                    after = page.next_cursor;
                    if (after && cursors.has(after)) return;
                    if (after) cursors.add(after);
                } while (after && alive());
            };
            const [currentDestination, currentSubscription] = await Promise.all([
                find<Destination>("destinations", (item) => item.destination_uid === destination.destination_uid),
                find<Subscription>("subscriptions", (item) => item.subscription_uid === subscription.subscription_uid),
            ]);
            if (!alive()) return;
            if (
                !currentDestination ||
                currentDestination.kind !== "personal" ||
                currentDestination.state !== "verified" ||
                currentDestination.revision !== destination.revision ||
                !currentSubscription ||
                currentSubscription.scope !== "personal" ||
                currentSubscription.state !== "enabled" ||
                currentSubscription.destination_uid !== destination.destination_uid ||
                currentSubscription.generation !== subscription.generation ||
                !currentSubscription.event_kinds.includes("test")
            )
                throw new Error("Selection changed");
            const pending: Operation = { context, subscription_uid: subscription.subscription_uid, request_id: crypto.randomUUID() };
            operation.current = pending;
            setUnresolved(true);
            setReceipt(null);
            setTitle("");
            setBody("");
            setConfirmed(false);
            try {
                const result = await api.post<{ event_uid: string }>(
                    `/settings/apps/notification-subscriptions/${encodeURIComponent(pending.subscription_uid)}/test`,
                    { expected_generation: subscription.generation, request_id: pending.request_id, title, body },
                    { env: { secretInput: true, interceptToast: true } as never, signal }
                );
                if (!alive()) return;
                pending.event_uid = result.data.event_uid;
            } catch {
                if (!alive()) return;
            }
            await reconcile(signal, alive);
        });
    };
    if (!visible || (!eligible && !unresolved && !receipt && !error)) return null;
    return (
        <section className="space-y-2 rounded-md border p-3 text-sm" aria-label={text("heading")} aria-busy={busy}>
            <h4 className="font-semibold">{text("heading")}</h4>
            <p>{text("help")}</p>
            {eligible && !denied && (
                <p className="break-words">
                    {text("destination")}: {destination.display_name || destination.destination_uid}
                </p>
            )}
            {eligible && !denied && !unresolved && (
                <>
                    <label className="block">
                        {text("title")}
                        <Input
                            value={title}
                            maxLength={120}
                            autoComplete="off"
                            disabled={busy}
                            onChange={(event) => {
                                setTitle(event.target.value);
                                setConfirmed(false);
                            }}
                        />
                    </label>
                    <label className="block">
                        {text("body")}
                        <Textarea
                            value={body}
                            maxLength={2000}
                            autoComplete="off"
                            disabled={busy}
                            onChange={(event) => {
                                setBody(event.target.value);
                                setConfirmed(false);
                            }}
                        />
                    </label>
                    <label className="block">
                        <input
                            className="checkbox checkbox-sm mr-2"
                            type="checkbox"
                            checked={confirmed}
                            disabled={busy}
                            onChange={(event) => setConfirmed(event.target.checked)}
                        />
                        {text("confirm")}
                    </label>
                    <Button type="button" size="sm" disabled={busy || !confirmed || !title.trim() || !body.trim()} onClick={send}>
                        {text("send")}
                    </Button>
                </>
            )}
            {unresolved && <p role="status">{text("unresolved")}</p>}
            {unresolved && (
                <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy || operation.current?.context !== context}
                    onClick={() => void run(reconcile)}
                >
                    {text("check")}
                </Button>
            )}
            {receipt && (
                <p role="status">
                    {text("result")}: {t(`card.outbox.${statuses.includes(receipt.status) ? receipt.status : "unavailable"}`)}
                </p>
            )}
            {error && <p role="alert">{text("error")}</p>}
        </section>
    );
}
