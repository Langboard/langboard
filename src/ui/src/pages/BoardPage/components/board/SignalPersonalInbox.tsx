import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import useBoardAppCatalog from "@/controllers/api/board/useBoardAppCatalog";
import BoardSettingsCredentialCustody from "../settings/BoardSettingsCredentialCustody";
import { canHandoffCustody, type CustodyMetadata } from "../settings/appCredentialCustodyState";
import { NOTIFICATION_CONNECT_READY, notificationContextForReady, type NotificationOnboardingContext } from "./notificationOnboarding";

type Connection = { connection_uid: string; state: string; revision: string };
type Page<T> = { items: T[]; next_cursor: string | null };
type Entry = { url: string; origin: string; protocol_version: number };
type Onboarding = {
    connection_uid: string;
    app_key: string;
    app_revision: string;
    connection_revision: string;
    available: boolean;
    entry: Entry | null;
};
type Mode = "personal" | "shared";
type Challenge = {
    kind: Mode;
    challenge_uid: string;
    project_uid?: string;
    state: string;
    revision: number;
    expires_at: string;
    proof_uid: string | null;
    destination_uid: string | null;
    proof: { external_identity: { issuer: string; subject: string }; destination: { display_name: string; external_id: string } } | null;
    challenge_token?: string;
};
type Destination = { kind: Mode; destination_uid: string; revision: number; state: string; display_name: string };
type Subscription = {
    scope: Mode;
    subscription_uid: string;
    destination_uid: string;
    state: string;
    generation: number;
    event_kinds: string[];
    project_uids: string[];
    private_deadline_project_uids?: string[];
};
const config = { env: { interceptToast: true } as never };
const oneShot = { env: { secretInput: true, interceptToast: true } as never };
const connectionPath = (uid: string) => `/settings/apps/connections/${encodeURIComponent(uid)}`;

export default function SignalPersonalInbox({ projectUID, userUID }: { projectUID: string; userUID: string }) {
    const [t] = useTranslation();
    const text = (key: string) => t(`card.personalInbox.${key}`);
    const catalog = useBoardAppCatalog(projectUID, userUID);
    const [mode, setMode] = useState<Mode>("personal");
    const [confirmRevoke, setConfirmRevoke] = useState<"destination" | "subscription" | null>(null);
    const [app, setApp] = useState("");
    const [connection, setConnection] = useState("");
    const [connections, setConnections] = useState<Connection[]>([]);
    const [connectionCursor, setConnectionCursor] = useState<string | null>(null);
    const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
    const [custody, setCustody] = useState<CustodyMetadata | null>(null);
    const [custodyBusy, setCustodyBusy] = useState(false);
    const [challenge, setChallenge] = useState<Challenge | null>(null);
    const [destinations, setDestinations] = useState<Destination[]>([]);
    const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
    const [destCursor, setDestCursor] = useState<string | null>(null);
    const [subCursor, setSubCursor] = useState<string | null>(null);
    const [destination, setDestination] = useState("");
    const [subscription, setSubscription] = useState("");
    const [deadline, setDeadline] = useState(false);
    const [privateDeadline, setPrivateDeadline] = useState(false);
    const [recoverySummary, setRecoverySummary] = useState<boolean>();
    const [busy, setBusy] = useState(false);
    const [uncertain, setUncertain] = useState(false);
    const [error, setError] = useState(false);
    const [confirmProof, setConfirmProof] = useState(false);
    const [phase, setPhase] = useState("");
    const ctrl = useRef(new AbortController());
    const generation = useRef(0);
    const writing = useRef(false);
    const popup = useRef<WindowProxy | null>(null);
    const secret = useRef<NotificationOnboardingContext | null>(null);
    const tokenRef = useRef<string | null>(null);
    const ready = useRef<{ origin: string; source: MessageEventSource | null; data: unknown } | null>(null);
    const expiry = useRef<ReturnType<typeof setTimeout> | null>(null);
    const sendReady = useRef<() => void>(() => {});
    const origin = useRef<string | null>(null);
    const posted = useRef(false);
    const uncertainApps = useRef(new Set<string>());
    const pendingLookup = useRef<{ connection: string; section: "challenges" | "subscriptions"; requestID: string } | null>(null);
    origin.current = onboarding?.entry?.origin ?? null;
    const cleanup = useCallback(() => {
        if (expiry.current) clearTimeout(expiry.current);
        expiry.current = null;
        secret.current = null;
        tokenRef.current = null;
        ready.current = null;
        popup.current?.close();
        popup.current = null;
        posted.current = false;
    }, []);
    const fence = useCallback(() => {
        generation.current++;
        ctrl.current.abort();
        ctrl.current = new AbortController();
        cleanup();
        writing.current = false;
        setBusy(false);
    }, [cleanup]);
    useEffect(() => {
        const pagehide = () => {
            generation.current++;
            ctrl.current.abort();
            cleanup();
        };
        window.addEventListener("pagehide", pagehide);
        const visibility = () => {
            if (document.visibilityState !== "visible") {
                fence();
                setOnboarding(null);
                setCustody(null);
                setChallenge(null);
                setDestinations([]);
                setSubscriptions([]);
                setDestination("");
                setSubscription("");
                setRecoverySummary(undefined);
                setConfirmProof(false);
                setPhase("");
            }
        };
        document.addEventListener("visibilitychange", visibility);
        const receive = (event: MessageEvent) => {
            const win = popup.current;
            const expectedOrigin = origin.current;
            if (posted.current || !win || !expectedOrigin || event.source !== win || event.origin !== expectedOrigin || win.closed) return;
            const data: unknown = event.data;
            if (
                !data ||
                typeof data !== "object" ||
                Array.isArray(data) ||
                Object.keys(data).length !== 2 ||
                !("type" in data) ||
                data.type !== NOTIFICATION_CONNECT_READY ||
                !("receiver_nonce" in data) ||
                typeof data.receiver_nonce !== "string" ||
                !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(data.receiver_nonce)
            )
                return;
            if (ready.current) return;
            ready.current = { origin: event.origin, source: event.source, data: { type: data.type, receiver_nonce: data.receiver_nonce } };
            sendReady.current();
        };
        window.addEventListener("message", receive);
        return () => {
            window.removeEventListener("message", receive);
            window.removeEventListener("pagehide", pagehide);
            document.removeEventListener("visibilitychange", visibility);
            pagehide();
        };
    }, [cleanup, fence]);
    const request = async <T,>(url: string, signal: AbortSignal, params?: Record<string, string>) =>
        (await api.get<T>(url, { ...config, signal, params })).data;
    const run = async (action: (signal: AbortSignal, current: number) => Promise<void>) => {
        if (writing.current) return;
        writing.current = true;
        setBusy(true);
        setError(false);
        const current = generation.current,
            signal = ctrl.current.signal;
        try {
            await action(signal, current);
        } catch {
            if (!signal.aborted && current === generation.current) setError(true);
        } finally {
            if (current === generation.current) {
                writing.current = false;
                setBusy(false);
            }
        }
    };
    const alive = (signal: AbortSignal, current: number) => !signal.aborted && current === generation.current;
    const resetConnection = () => {
        setConnection("");
        setOnboarding(null);
        setCustody(null);
        setChallenge(null);
        setDestinations([]);
        setSubscriptions([]);
        setDestCursor(null);
        setSubCursor(null);
        setDestination("");
        setSubscription("");
        setRecoverySummary(undefined);
        setDeadline(false);
        setPrivateDeadline(false);
        setConfirmRevoke(null);
        setConfirmProof(false);
        setPhase("");
    };
    const listConnections = (key: string, after?: string, reconcile = false) =>
        run(async (signal, current) => {
            const page = await request<Page<Connection>>(
                `/settings/apps/registry/${encodeURIComponent(key)}/inbound-connections`,
                signal,
                after ? { after } : undefined
            );
            if (!alive(signal, current)) return;
            setConnections((old) => (after ? [...old, ...page.items] : page.items));
            setConnectionCursor(page.next_cursor);
            if (reconcile) {
                uncertainApps.current.delete(key);
                setUncertain(false);
            }
        });
    const chooseApp = (key: string) => {
        fence();
        resetConnection();
        setApp(key);
        setConnections([]);
        setConnectionCursor(null);
        setUncertain(uncertainApps.current.has(key));
        setError(false);
        if (key) void listConnections(key);
    };
    const readSettings = async (uid: string, signal: AbortSignal) => {
        const root = connectionPath(uid);
        const [meta, auth, dest, subs] = await Promise.all([
            request<Onboarding>(`${root}/notification-onboarding`, signal),
            request<CustodyMetadata>(`${root}/credential-custody`, signal),
            request<Page<Destination>>(`${root}/notification-settings`, signal, { section: "destinations" }),
            request<Page<Subscription>>(`${root}/notification-settings`, signal, { section: "subscriptions" }),
        ]);
        return { meta, auth, dest, subs };
    };
    const refresh = (uid = connection) =>
        run(async (signal, current) => {
            const pending = pendingLookup.current;
            if (pending?.connection === uid) {
                await request(`${connectionPath(uid)}/notification-settings`, signal, { section: pending.section, request_id: pending.requestID });
                pendingLookup.current = null;
            }
            const data = await readSettings(uid, signal);
            if (!alive(signal, current)) return;
            setOnboarding(data.meta);
            setCustody(data.auth);
            setDestinations(data.dest.items);
            setSubscriptions(data.subs.items);
            setDestCursor(data.dest.next_cursor);
            setSubCursor(data.subs.next_cursor);
            setRecoverySummary(undefined);
            setUncertain(false);
        });
    const chooseMode = (next: Mode) => {
        if (next === mode || writing.current || custodyBusy) return;
        fence();
        setMode(next);
        setChallenge(null);
        setDestinations([]);
        setSubscriptions([]);
        setDestCursor(null);
        setSubCursor(null);
        setDestination("");
        setSubscription("");
        setRecoverySummary(undefined);
        setDeadline(false);
        setPrivateDeadline(false);
        setConfirmProof(false);
        setConfirmRevoke(null);
        setPhase("");
        setOnboarding(null);
        setCustody(null);
        setError(false);
        if (connection) refresh(connection);
    };
    const chooseConnection = (uid: string) => {
        fence();
        resetConnection();
        setConnection(uid);
        setError(false);
        setUncertain(false);
        if (uid) refresh(uid);
    };
    const moreSettings = (section: "destinations" | "subscriptions", after: string) =>
        run(async (signal, current) => {
            if (section === "destinations") {
                const page = await request<Page<Destination>>(`${connectionPath(connection)}/notification-settings`, signal, { section, after });
                if (alive(signal, current)) {
                    setDestinations((old) => [...old, ...page.items]);
                    setDestCursor(page.next_cursor);
                }
            } else {
                const page = await request<Page<Subscription>>(`${connectionPath(connection)}/notification-settings`, signal, { section, after });
                if (alive(signal, current)) {
                    setSubscriptions((old) => [...old, ...page.items]);
                    setSubCursor(page.next_cursor);
                }
            }
        });
    const createConnection = () =>
        run(async (signal, current) => {
            const selected = catalog.data?.find((item) => item.key === app);
            if (!selected?.app_revision) return;
            setUncertain(true);
            try {
                await api.post(
                    `/settings/apps/registry/${encodeURIComponent(app)}/inbound-connections`,
                    { app_revision: selected.app_revision },
                    { ...oneShot, signal }
                );
            } catch {
                if (alive(signal, current)) {
                    uncertainApps.current.add(app);
                    setUncertain(true);
                }
                return;
            }
            const page = await request<Page<Connection>>(`/settings/apps/registry/${encodeURIComponent(app)}/inbound-connections`, signal);
            if (alive(signal, current)) {
                setConnections(page.items);
                setConnectionCursor(page.next_cursor);
                setUncertain(false);
            }
        });
    const begin = () => {
        const entry = onboarding?.available && onboarding.entry;
        if (!entry || entry.protocol_version !== 1 || !canHandoffCustody(custody ?? undefined) || uncertain || writing.current) return;
        let parsed: URL;
        try {
            parsed = new URL(entry.url);
        } catch {
            return;
        }
        if (
            parsed.protocol !== "https:" ||
            parsed.origin !== entry.origin ||
            parsed.username ||
            parsed.password ||
            parsed.search ||
            parsed.hash ||
            /\s/.test(entry.url) ||
            [...entry.url].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
        )
            return;
        const win = window.open(entry.url, "_blank", "popup,width=560,height=720");
        if (!win) {
            setError(true);
            return;
        }
        popup.current = win;
        void run(async (signal, current) => {
            try {
                const root = connectionPath(connection);
                const [meta, auth] = await Promise.all([
                    request<Onboarding>(`${root}/notification-onboarding`, signal),
                    request<CustodyMetadata>(`${root}/credential-custody`, signal),
                ]);
                if (!alive(signal, current)) return;
                if (
                    !meta.available ||
                    meta.entry?.url !== entry.url ||
                    meta.entry.origin !== entry.origin ||
                    !canHandoffCustody(auth) ||
                    !auth.custody
                )
                    throw new Error("Changed");
                const challengeRequest = crypto.randomUUID();
                pendingLookup.current = { connection, section: "challenges", requestID: challengeRequest };
                setUncertain(true);
                let issued: Challenge;
                try {
                    issued = (
                        await api.post<Challenge>(
                            `${root}/notification-destination-challenges`,
                            {
                                request_id: challengeRequest,
                                expected_connection_revision: meta.connection_revision,
                                kind: mode,
                                ...(mode === "shared" ? { project_uid: projectUID } : {}),
                            },
                            { ...oneShot, signal }
                        )
                    ).data;
                } catch {
                    if (alive(signal, current)) setUncertain(true);
                    const result = await request<Page<Challenge>>(`${root}/notification-settings`, signal, {
                        section: "challenges",
                        request_id: challengeRequest,
                    });
                    if (alive(signal, current)) {
                        pendingLookup.current = null;
                        if (result.items.length === 1 && result.items[0].kind === mode) setChallenge(result.items[0]);
                    }
                    return;
                }
                pendingLookup.current = null;
                if (
                    !alive(signal, current) ||
                    issued.kind !== mode ||
                    !issued.challenge_token ||
                    (mode === "shared" && issued.project_uid !== projectUID)
                )
                    throw new Error("Unavailable");
                tokenRef.current = issued.challenge_token;
                delete issued.challenge_token;
                setChallenge(issued);
                const handoffRequest = crypto.randomUUID();
                let handoff: { handoff_uid: string; state: string };
                try {
                    handoff = (
                        await api.post<{ handoff_uid: string; state: string }>(
                            `/settings/apps/credential-custody/${auth.custody.custody_uid}/handoff`,
                            {
                                expected_revision: auth.custody.revision,
                                request_id: handoffRequest,
                                notification_challenge_uid: issued.challenge_uid,
                                notification_challenge_token: tokenRef.current,
                            },
                            { ...oneShot, signal }
                        )
                    ).data;
                } catch {
                    if (alive(signal, current)) setUncertain(true);
                    await request<CustodyMetadata>(`${root}/credential-custody`, signal);
                    return;
                }
                if (handoff.state !== "delivered") throw new Error("Undelivered");
                const [latest, checked, latestCustody] = await Promise.all([
                    request<Onboarding>(`${root}/notification-onboarding`, signal),
                    request<Challenge>(`/settings/apps/notification-destination-challenges/${issued.challenge_uid}`, signal),
                    request<CustodyMetadata>(`${root}/credential-custody`, signal),
                ]);
                if (
                    !alive(signal, current) ||
                    latest.connection_revision !== meta.connection_revision ||
                    latest.app_revision !== meta.app_revision ||
                    latest.entry?.url !== entry.url ||
                    latest.entry.origin !== entry.origin ||
                    checked.kind !== mode ||
                    checked.project_uid !== issued.project_uid ||
                    checked.state !== "pending" ||
                    checked.expires_at !== issued.expires_at ||
                    latestCustody.last_handoff?.handoff_uid !== handoff.handoff_uid ||
                    latestCustody.last_handoff.state !== "delivered" ||
                    latestCustody.custody?.revision !== auth.custody.revision
                )
                    throw new Error("Changed");
                secret.current = {
                    handoff_uid: handoff.handoff_uid,
                    challenge_uid: issued.challenge_uid,
                    challenge_token: tokenRef.current!,
                    expires_at: checked.expires_at,
                };
                const delay = Date.parse(checked.expires_at) - Date.now();
                if (!Number.isFinite(delay) || delay <= 0) throw new Error("Expired");
                expiry.current = setTimeout(cleanup, delay);
                setPhase("waitingProof");
                setUncertain(false);
                sendReady.current();
            } catch (error) {
                cleanup();
                throw error;
            } finally {
                if (!secret.current && !posted.current) cleanup();
            }
        });
    };
    sendReady.current = () => {
        if (!ready.current || !popup.current || !secret.current || !onboarding?.entry) return;
        const message = notificationContextForReady(ready.current, popup.current, onboarding.entry.origin, secret.current);
        if (!message) {
            cleanup();
            return;
        }
        popup.current.postMessage(message, onboarding.entry.origin);
        ready.current = null;
        secret.current = null;
        tokenRef.current = null;
        posted.current = true;
    };
    const refreshChallenge = () =>
        challenge &&
        run(async (signal, current) => {
            const data = await request<Challenge>(`/settings/apps/notification-destination-challenges/${challenge.challenge_uid}`, signal);
            if (alive(signal, current) && data.kind === mode) {
                setChallenge(data);
                setConfirmProof(false);
            }
        });
    const approve = () =>
        challenge?.kind === mode &&
        challenge?.proof_uid &&
        confirmProof &&
        run(async (signal, current) => {
            setUncertain(true);
            let result: Destination | null = null;
            try {
                result = (
                    await api.post<Destination>(
                        `/settings/apps/notification-destination-challenges/${challenge.challenge_uid}/approve`,
                        { expected_revision: challenge.revision, proof_uid: challenge.proof_uid },
                        { ...oneShot, signal }
                    )
                ).data;
            } catch {
                if (alive(signal, current)) setUncertain(true);
            }
            const [checked, data] = await Promise.all([
                request<Challenge>(`/settings/apps/notification-destination-challenges/${challenge.challenge_uid}`, signal),
                readSettings(connection, signal),
            ]);
            if (alive(signal, current)) {
                setChallenge(checked);
                setDestinations(data.dest.items);
                setSubscriptions(data.subs.items);
                setConfirmProof(false);
                if (
                    result &&
                    checked.state === "verified" &&
                    checked.destination_uid === result.destination_uid &&
                    data.dest.items.some(
                        (item) => item.destination_uid === result?.destination_uid && item.revision === result?.revision && item.state === "verified"
                    )
                )
                    setUncertain(false);
            }
        });
    const configure = async (selected: Subscription, includeDeadline: boolean, signal: AbortSignal) => {
        const projects = selected.project_uids.filter((uid) => uid !== projectUID);
        if (includeDeadline) projects.push(projectUID);
        const privateProjects = (selected.private_deadline_project_uids ?? []).filter((uid) => uid !== projectUID);
        if (mode === "personal" && includeDeadline && privateDeadline) privateProjects.push(projectUID);
        const kinds =
            mode === "shared"
                ? ["test"]
                : Array.from(new Set([...selected.event_kinds, "test", "card_activity"])).filter(
                      (kind) => kind !== "card_deadline" && kind !== "missed_summary"
                  );
        if (includeDeadline || (selected.event_kinds.includes("card_deadline") && projects.length > 0)) kinds.push("card_deadline");
        if ((mode === "personal" || kinds.includes("card_deadline")) && (recoverySummary ?? selected.event_kinds.includes("missed_summary"))) {
            kinds.push("missed_summary");
        }
        return (
            await api.post<Subscription>(
                `/settings/apps/notification-subscriptions/${selected.subscription_uid}/scope`,
                {
                    expected_generation: selected.generation,
                    event_kinds: kinds,
                    project_uids: projects,
                    ...(mode === "personal" && (privateDeadline || selected.private_deadline_project_uids?.length)
                        ? { private_deadline_project_uids: privateProjects }
                        : {}),
                },
                { ...oneShot, signal }
            )
        ).data;
    };
    const optIn = () =>
        run(async (signal, current) => {
            setPhase("");
            const selectedDestination = destinations.find(
                (item) => item.destination_uid === destination && item.state === "verified" && item.kind === mode
            );
            if (!selectedDestination) return;
            let selected = subscriptions.find(
                (item) =>
                    item.subscription_uid === subscription && item.destination_uid === destination && item.state === "enabled" && item.scope === mode
            );
            if (subscription && !selected) return;
            if (mode === "shared" && !deadline && (!selected || !selected.project_uids.some((uid) => uid !== projectUID))) return;
            setUncertain(true);
            if (!selected) {
                const requestID = crypto.randomUUID();
                pendingLookup.current = { connection, section: "subscriptions", requestID };
                try {
                    selected = (
                        await api.post<Subscription>(
                            "/settings/apps/notification-subscriptions",
                            {
                                destination_uid: destination,
                                expected_destination_revision: selectedDestination.revision,
                                request_id: requestID,
                                ...(mode === "shared" ? { scope: "shared", project_uids: [projectUID] } : { scope: "personal" }),
                            },
                            { ...oneShot, signal }
                        )
                    ).data;
                } catch {
                    if (alive(signal, current)) setUncertain(true);
                    const reconciled = await request<Page<Subscription>>(`${connectionPath(connection)}/notification-settings`, signal, {
                        section: "subscriptions",
                        request_id: requestID,
                    });
                    if (alive(signal, current)) {
                        pendingLookup.current = null;
                        setSubscriptions(reconciled.items);
                    }
                    return;
                }
                const readback = await request<Page<Subscription>>(`${connectionPath(connection)}/notification-settings`, signal, {
                    section: "subscriptions",
                    request_id: requestID,
                });
                pendingLookup.current = null;
                const receipt = selected;
                const matched = readback.items.find(
                    (item) => item.subscription_uid === receipt.subscription_uid && item.generation === receipt.generation && item.scope === mode
                );
                if (!matched) {
                    if (alive(signal, current)) setUncertain(true);
                    return;
                }
                selected = matched;
            }
            if (!alive(signal, current)) return;
            let result: Subscription | null = null;
            try {
                result = await configure(selected, deadline, signal);
            } catch {
                if (alive(signal, current)) setUncertain(true);
            }
            const data = await readSettings(connection, signal);
            if (alive(signal, current)) {
                setDestinations(data.dest.items);
                setSubscriptions(data.subs.items);
                setSubscription(selected.subscription_uid);
                if (
                    result &&
                    data.subs.items.some((item) => item.subscription_uid === result?.subscription_uid && item.generation === result?.generation)
                ) {
                    setPhase("saved");
                    setRecoverySummary(undefined);
                    setUncertain(false);
                } else setUncertain(true);
            }
        });
    const revoke = (kind: "destination" | "subscription") =>
        run(async (signal, current) => {
            const dest = destinations.find((item) => item.destination_uid === destination);
            const sub = subscriptions.find((item) => item.subscription_uid === subscription);
            setUncertain(true);
            let result: Destination | Subscription | null = null;
            if (kind === "destination" && dest) {
                try {
                    result = (
                        await api.post<Destination>(
                            `/settings/apps/notification-destinations/${dest.destination_uid}/revoke`,
                            { expected_revision: dest.revision },
                            { ...oneShot, signal }
                        )
                    ).data;
                } catch {
                    if (alive(signal, current)) setUncertain(true);
                }
            } else if (kind === "subscription" && sub) {
                try {
                    result = (
                        await api.post<Subscription>(
                            `/settings/apps/notification-subscriptions/${sub.subscription_uid}/revoke`,
                            { expected_generation: sub.generation },
                            { ...oneShot, signal }
                        )
                    ).data;
                } catch {
                    if (alive(signal, current)) setUncertain(true);
                }
            } else return;
            const data = await readSettings(connection, signal);
            if (alive(signal, current)) {
                setDestinations(data.dest.items);
                setSubscriptions(data.subs.items);
                if (
                    result &&
                    (kind === "destination"
                        ? data.dest.items.some(
                              (item) =>
                                  item.destination_uid === dest?.destination_uid &&
                                  item.state === "revoked" &&
                                  item.revision === (result as Destination).revision
                          )
                        : data.subs.items.some(
                              (item) =>
                                  item.subscription_uid === sub?.subscription_uid &&
                                  item.state === "revoked" &&
                                  item.generation === (result as Subscription).generation
                          ))
                ) {
                    if (kind === "destination") setDestination("");
                    setSubscription("");
                    setRecoverySummary(undefined);
                    setDeadline(false);
                    setPrivateDeadline(false);
                    setPhase("");
                    setUncertain(false);
                }
            }
        });
    const selectedSubscription = subscriptions.find((item) => item.subscription_uid === subscription);
    const availableApps = (catalog.data ?? []).filter(
        (item) => item.capabilities.includes("events.receive") && item.is_available !== false && item.app_revision
    );
    return (
        <section className="space-y-3 border-b p-3" aria-busy={busy || custodyBusy}>
            <h3 className="font-semibold">{text("title")}</h3>
            <p className="text-sm">{text("help")}</p>
            <label className="block text-sm">
                {text("mode")}
                <select
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    value={mode}
                    disabled={busy || custodyBusy}
                    onChange={(event) => chooseMode(event.target.value as Mode)}
                >
                    <option value="personal">{text("personalMode")}</option>
                    <option value="shared">{text("sharedMode")}</option>
                </select>
            </label>
            {mode === "shared" && <p className="text-sm">{text("sharedHelp")}</p>}
            {catalog.isError && <p role="alert">{text("error")}</p>}
            <label className="block text-sm">
                {text("app")}
                <select
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    value={app}
                    disabled={busy}
                    onChange={(event) => chooseApp(event.target.value)}
                >
                    <option value="">{text("chooseApp")}</option>
                    {availableApps.map((item) => (
                        <option key={item.key} value={item.key}>
                            {item.name}
                        </option>
                    ))}
                </select>
            </label>
            {app && (
                <>
                    <label className="block text-sm">
                        {text("connection")}
                        <select
                            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            value={connection}
                            disabled={busy}
                            onChange={(event) => chooseConnection(event.target.value)}
                        >
                            <option value="">{text("chooseConnection")}</option>
                            {connections
                                .filter((item) => item.state === "connected")
                                .map((item) => (
                                    <option key={item.connection_uid} value={item.connection_uid}>
                                        {item.connection_uid}
                                    </option>
                                ))}
                        </select>
                    </label>
                    <div className="flex flex-wrap gap-2">
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => {
                                void listConnections(app, undefined, true);
                            }}
                        >
                            {text("refreshConnections")}
                        </Button>
                        {connectionCursor && (
                            <Button size="sm" variant="outline" disabled={busy} onClick={() => void listConnections(app, connectionCursor)}>
                                {text("more")}
                            </Button>
                        )}
                        <Button size="sm" variant="outline" disabled={busy || uncertain} onClick={createConnection}>
                            {text("createConnection")}
                        </Button>
                    </div>
                </>
            )}
            {connection && (
                <>
                    <Button size="sm" variant="outline" disabled={busy || custodyBusy} onClick={() => refresh()}>
                        {text("refresh")}
                    </Button>
                    <BoardSettingsCredentialCustody
                        key={connection}
                        connectionUID={connection}
                        userUID={userUID}
                        canConfigure
                        onBusy={setCustodyBusy}
                        showHandoff={false}
                    />
                    {onboarding && !onboarding.available && <p>{text("unavailable")}</p>}
                    {onboarding?.available && (
                        <>
                            <p className="text-sm">{text("custodyHelp")}</p>
                            <Button size="sm" disabled={busy || custodyBusy || uncertain || !canHandoffCustody(custody ?? undefined)} onClick={begin}>
                                {text("connect")}
                            </Button>
                        </>
                    )}
                    {phase === "waitingProof" && <p role="status">{text("waitingProof")}</p>}
                    {challenge && (
                        <>
                            <Button size="sm" variant="outline" disabled={busy} onClick={refreshChallenge}>
                                {text("refreshProof")}
                            </Button>
                            {challenge.state === "awaiting_user_approval" && challenge.proof && (
                                <div className="space-y-2 rounded-md border p-3">
                                    <p>{text("reviewProof")}</p>
                                    <p className="break-all">
                                        {text("identity")}: {challenge.proof.external_identity.issuer}
                                    </p>
                                    <p className="break-all">
                                        {text("destination")}: {challenge.proof.destination.display_name}
                                    </p>
                                    <label className="flex items-center gap-2">
                                        <input type="checkbox" checked={confirmProof} onChange={(event) => setConfirmProof(event.target.checked)} />
                                        {text("confirmProof")}
                                    </label>
                                    <Button size="sm" disabled={busy || uncertain || !confirmProof} onClick={approve}>
                                        {text("approve")}
                                    </Button>
                                </div>
                            )}
                        </>
                    )}
                    <label className="block text-sm">
                        {text("destination")}
                        <select
                            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            value={destination}
                            disabled={busy}
                            onChange={(event) => {
                                setDestination(event.target.value);
                                setSubscription("");
                                setRecoverySummary(undefined);
                                setDeadline(false);
                                setPrivateDeadline(false);
                                setConfirmRevoke(null);
                            }}
                        >
                            <option value="">{text("chooseDestination")}</option>
                            {destinations
                                .filter((item) => item.state === "verified" && item.kind === mode)
                                .map((item) => (
                                    <option key={item.destination_uid} value={item.destination_uid}>
                                        {item.display_name || item.destination_uid}
                                    </option>
                                ))}
                        </select>
                    </label>
                    {destCursor && (
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => moreSettings("destinations", destCursor)}>
                            {text("more")}
                        </Button>
                    )}
                    {destination && (
                        <>
                            <label className="block text-sm">
                                {text("subscription")}
                                <select
                                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    value={subscription}
                                    disabled={busy}
                                    onChange={(event) => {
                                        const uid = event.target.value;
                                        setSubscription(uid);
                                        setRecoverySummary(undefined);
                                        const selected = subscriptions.find((item) => item.subscription_uid === uid && item.scope === mode);
                                        setDeadline(
                                            !!selected?.project_uids.includes(projectUID) &&
                                                (mode === "personal" || selected.event_kinds.includes("card_deadline"))
                                        );
                                        setPrivateDeadline(mode === "personal" && !!selected?.private_deadline_project_uids?.includes(projectUID));
                                        setConfirmRevoke(null);
                                    }}
                                >
                                    <option value="">{text("newSubscription")}</option>
                                    {subscriptions
                                        .filter((item) => item.destination_uid === destination && item.state === "enabled" && item.scope === mode)
                                        .map((item) => (
                                            <option key={item.subscription_uid} value={item.subscription_uid}>
                                                {item.subscription_uid}
                                            </option>
                                        ))}
                                </select>
                            </label>
                            {subCursor && (
                                <Button size="sm" variant="outline" disabled={busy} onClick={() => moreSettings("subscriptions", subCursor)}>
                                    {text("more")}
                                </Button>
                            )}
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={deadline}
                                    onChange={(event) => {
                                        setDeadline(event.target.checked);
                                        if (!event.target.checked) setPrivateDeadline(false);
                                    }}
                                />
                                {text("deadline")}
                            </label>
                            {mode === "personal" && deadline && (
                                <label className="flex items-center gap-2">
                                    <input type="checkbox" checked={privateDeadline} onChange={(event) => setPrivateDeadline(event.target.checked)} />
                                    {text("privateDeadline")}
                                </label>
                            )}
                            {(mode === "personal" || deadline || selectedSubscription?.project_uids.some((uid) => uid !== projectUID)) && (
                                <label className="flex items-center gap-2">
                                    <input
                                        type="checkbox"
                                        checked={recoverySummary ?? !!selectedSubscription?.event_kinds.includes("missed_summary")}
                                        disabled={busy || uncertain}
                                        onChange={(event) => setRecoverySummary(event.target.checked)}
                                    />
                                    {text("recoverySummary")}
                                </label>
                            )}
                            <Button
                                size="sm"
                                disabled={
                                    busy ||
                                    uncertain ||
                                    (mode === "shared" && !deadline && !selectedSubscription?.project_uids.some((uid) => uid !== projectUID))
                                }
                                onClick={optIn}
                            >
                                {text("optIn")}
                            </Button>
                            <Button size="sm" variant="outline" disabled={busy || uncertain} onClick={() => setConfirmRevoke("destination")}>
                                {text("revokeDestination")}
                            </Button>
                            {selectedSubscription && (
                                <Button size="sm" variant="outline" disabled={busy || uncertain} onClick={() => setConfirmRevoke("subscription")}>
                                    {text("revokeSubscription")}
                                </Button>
                            )}
                            {confirmRevoke && (
                                <div className="space-y-2 rounded-md border p-3">
                                    <p>{text(confirmRevoke === "destination" ? "confirmRevokeDestination" : "confirmRevokeSubscription")}</p>
                                    <Button
                                        size="sm"
                                        disabled={busy || uncertain}
                                        onClick={() => {
                                            void revoke(confirmRevoke);
                                            setConfirmRevoke(null);
                                        }}
                                    >
                                        {text("confirm")}
                                    </Button>
                                    <Button size="sm" variant="ghost" onClick={() => setConfirmRevoke(null)}>
                                        {text("cancel")}
                                    </Button>
                                </div>
                            )}
                        </>
                    )}
                    {phase === "saved" && <p role="status">{text("saved")}</p>}
                </>
            )}
            {uncertain && <p role="alert">{text("uncertain")}</p>}
            {error && <p role="alert">{text("error")}</p>}
        </section>
    );
}
