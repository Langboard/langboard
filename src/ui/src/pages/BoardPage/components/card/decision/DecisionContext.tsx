import { api } from "@/core/helpers/Api";
import { getAuthStore } from "@/core/stores/AuthStore";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export type PollResponse = {
    actor_uid: string;
    choice: string;
    option_uid: string | null;
    response_revision: number;
    response_order: number;
    responded_at: string;
};
export type Decision = {
    uid: string;
    round_id: string;
    source_kind: string;
    source_ref: string;
    approval_uid?: string | null;
    automatic_resume?: boolean;
    question: string;
    revision: number;
    target_revision: string;
    policy_revision: string;
    participant_uids: string[];
    policy: unknown;
    state: string;
    deadline: string | null;
    active_responders: string[];
    allowed_actions: string[];
    blocked_reason: string | null;
    effect_result: unknown;
    poll?: {
        options: { uid: string; key: string; label: string }[];
        settings: Record<string, unknown>;
        phase: "open" | "closed";
        my_response: PollResponse | null;
        can_change: boolean;
        tally_visible: boolean;
        tally: {
            participation_count: number;
            vote_count: number;
            abstain_count: number;
            needs_info_count: number;
            counts_by_option: Record<string, number>;
        } | null;
        responses: PollResponse[] | null;
        winner_option_uid: string | null;
        outcome_reason: string | null;
    };
};
type DecisionPage = { items: Decision[]; next_cursor: string | null; target_revision: string };
type Command = { url: string; data: Record<string, unknown> };
type Context = {
    projectUID: string;
    cardUID: string;
    userUID: string;
    page: DecisionPage | undefined;
    busy: boolean;
    locked: boolean;
    stale: boolean;
    loading: boolean;
    message: string;
    reconfirm: number;
    pending: Command | undefined;
    execute: (url: string, data: Record<string, unknown>) => Promise<boolean>;
    retry: () => void;
    reload: () => void;
    next: () => void;
    previous: () => void;
    hasPrevious: boolean;
    reportError: (error: unknown, session: number) => void;
};
const DecisionContext = createContext<Context | null>(null);
export const useDecisions = () => {
    const context = useContext(DecisionContext);
    if (!context) throw new Error("Decision provider required");
    return context;
};
export function DecisionProvider(props: {
    projectUID: string;
    cardUID: string;
    userUID: string;
    sourceRevision?: number;
    children: React.ReactNode;
}) {
    return <ScopedDecisionProvider key={`${props.projectUID}:${props.cardUID}:${props.userUID}`} {...props} />;
}
function ScopedDecisionProvider({ projectUID, cardUID, userUID, sourceRevision, children }: Parameters<typeof DecisionProvider>[0]) {
    const [t] = useTranslation();
    const [page, setPage] = useState<DecisionPage>();
    const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
    const [refresh, setRefresh] = useState(0);
    const [reconfirm, setReconfirm] = useState(0);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [stale, setStale] = useState(false);
    const [messageKey, setMessageKey] = useState("");
    const [pending, setPending] = useState<Command>();
    const mounted = useRef(true);
    const submitting = useRef(false);
    const generation = useRef(0);
    const cursor = cursors.at(-1);
    const base = `/board/${projectUID}/card/${cardUID}/decisions`;
    const invalidate = (error: unknown, session: number) => {
        const status = session === getAuthStore().getSessionVersion() ? (error as { response?: { status?: number } }).response?.status : 401;
        if ([401, 403, 404, 409].includes(status ?? 0)) {
            setStale(true);
            setPending(undefined);
            setReconfirm((value) => value + 1);
            setMessageKey("card.decision.Reload required");
        } else {
            if (status && status < 500) setPending(undefined);
            setMessageKey("card.decision.Request failed");
        }
    };
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            generation.current++;
        };
    }, []);
    useEffect(() => {
        if (stale) return;
        let current = true;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        setLoading(true);
        api.get(base, { params: { limit: 15, before: cursor }, signal: controller.signal })
            .then(({ data }) => {
                if (!current) return;
                if (session !== getAuthStore().getSessionVersion()) {
                    invalidate(undefined, session);
                    return;
                }
                setPage(data);
            })
            .catch((error) => {
                if (!current) return;
                invalidate(error, session);
                setLoading(false);
                setStale(true);
                setPending(undefined);
                setReconfirm((value) => value + 1);
                setMessageKey("card.decision.Reload required");
            })
            .finally(() => {
                if (current) setLoading(false);
            });
        return () => {
            current = false;
            controller.abort();
        };
    }, [base, cursor, refresh, sourceRevision, stale]);
    const run = async (command: Command) => {
        if (submitting.current || loading || stale) return false;
        submitting.current = true;
        setBusy(true);
        setPending(command);
        setMessageKey("");
        const epoch = generation.current;
        const session = getAuthStore().getSessionVersion();
        try {
            await api.post(command.url, command.data);
            if (!mounted.current || epoch !== generation.current) return false;
            if (session !== getAuthStore().getSessionVersion()) {
                invalidate(undefined, session);
                return false;
            }
            setLoading(true);
            setPending(undefined);
            setReconfirm((value) => value + 1);
            setCursors([undefined]);
            setRefresh((value) => value + 1);
            setMessageKey("card.decision.Saved");
            return true;
        } catch (error) {
            if (mounted.current && epoch === generation.current) invalidate(error, session);
            return false;
        } finally {
            if (mounted.current && epoch === generation.current) {
                submitting.current = false;
                setBusy(false);
            }
        }
    };
    const reload = () => {
        if (submitting.current) return;
        setLoading(true);
        setPending(undefined);
        setStale(false);
        setMessageKey("");
        setCursors([undefined]);
        setReconfirm((value) => value + 1);
        setRefresh((value) => value + 1);
    };
    return (
        <DecisionContext.Provider
            value={{
                projectUID,
                cardUID,
                userUID,
                page,
                busy,
                stale,
                loading,
                message: messageKey ? t(messageKey) : "",
                pending,
                reconfirm,
                locked: busy || loading || stale || !!pending,
                execute: (url, data) =>
                    pending || submitting.current || loading || stale
                        ? Promise.resolve(false)
                        : run({ url, data: { ...data, request_id: crypto.randomUUID() } }),
                retry: () => {
                    if (pending) void run(pending);
                },
                reload,
                next: () => {
                    if (page?.next_cursor) {
                        setLoading(true);
                        setCursors([...cursors, page.next_cursor]);
                    }
                },
                previous: () => {
                    if (cursors.length > 1) {
                        setLoading(true);
                        setCursors(cursors.slice(0, -1));
                    }
                },
                hasPrevious: cursors.length > 1,
                reportError: invalidate,
            }}
        >
            {children}
        </DecisionContext.Provider>
    );
}
