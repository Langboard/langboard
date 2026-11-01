import { api } from "@/core/helpers/Api";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { Routing } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CommentCompaction, CommentCompactionEligibility } from "./CommentCompaction.types";

export default function useCommentCompaction(
    eligibility: CommentCompactionEligibility | undefined,
    inFlight: CommentCompaction[],
    refresh: () => void
) {
    const { projectUID, card, currentUser } = useBoardCard();
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const scope = `${identity}/${currentUser.uid}/${projectUID}/${card.uid}`;
    const currentScope = useRef(scope);
    currentScope.current = scope;
    const controller = useRef<AbortController | undefined>(undefined);
    const [operation, setOperation] = useState<{ scope: string; record: CommentCompaction }>();
    const [failure, setFailure] = useState<{ scope: string; error: unknown }>();
    const [cancelling, setCancelling] = useState(false);
    const serial = useRef(0);
    const [epoch, setEpoch] = useState(0);
    const advance = useCallback(() => {
        serial.current += 1;
        setEpoch(serial.current);
        return serial.current;
    }, []);
    const record = operation?.scope === scope ? operation.record : inFlight.find((item) => item.request_key);
    const active = !!record && ["pending", "running"].includes(record.status);
    const base = `${Utils.String.format(Routing.API.BOARD.CARD.GET_DETAILS, { uid: projectUID, card_uid: card.uid })}/comment-compactions`;
    const ownsScope = useCallback(
        () =>
            currentScope.current === scope &&
            useAuthStore.getState().currentUser?.uid === currentUser.uid &&
            useAuthStore.getState().getIdentityVersion() === identity,
        [scope, currentUser.uid, identity]
    );

    useEffect(() => {
        setCancelling(false);
        return () => controller.current?.abort();
    }, [scope]);

    const watch = useQuery({
        queryKey: ["comment-compaction-status", scope, epoch, record?.request_key, record?.uid],
        enabled: active,
        retry: false,
        gcTime: 0,
        refetchInterval: active ? 1500 : false,
        refetchOnWindowFocus: false,
        queryFn: async ({ signal }) => {
            let found: CommentCompaction | undefined;
            if (record?.uid) found = (await api.get<CommentCompaction>(`${base}/${encodeURIComponent(record.uid)}`, { signal })).data;
            else {
                const { data } = await api.get<{ records: CommentCompaction[] }>(base, { signal, params: { request_key: record?.request_key } });
                found = data.records.find((item) => item.request_key === record?.request_key);
            }
            if (!ownsScope() || signal.aborted || serial.current !== epoch) throw new DOMException("Compaction request cancelled", "AbortError");
            return found ?? null;
        },
    });

    useEffect(() => {
        if (!watch.data || !ownsScope()) return;
        setOperation({ scope, record: watch.data });
        if (!["pending", "running"].includes(watch.data.status)) {
            advance();
            controller.current?.abort();
            refresh();
        }
    }, [watch.data, scope, refresh, ownsScope, advance]);

    const generate = async (retry = false) => {
        const key = retry ? record?.request_key : eligibility?.next_request_key;
        if (!key || active || !eligibility?.can_generate || !eligibility.provider_configured) return;
        const attempt = advance();
        controller.current?.abort();
        const request = new AbortController();
        controller.current = request;
        setFailure(undefined);
        setOperation({ scope, record: { uid: "", request_key: key, status: "pending" } });
        try {
            const { data } = await api.post<CommentCompaction>(base, { expected_request_key: key, retry }, { signal: request.signal });
            if (!ownsScope() || request.signal.aborted || serial.current !== attempt) return;
            setOperation({ scope, record: data });
            if (!["pending", "running"].includes(data.status)) {
                advance();
                refresh();
            }
        } catch (error) {
            if (!ownsScope() || request.signal.aborted || serial.current !== attempt) return;
            setFailure({ scope, error });
            setOperation({ scope, record: { uid: "", request_key: key, status: "failed" } });
            refresh();
        }
    };

    const cancel = async () => {
        if (!record?.uid || !active || cancelling) return;
        setCancelling(true);
        advance();
        try {
            const { data } = await api.post<CommentCompaction>(`${base}/${encodeURIComponent(record.uid)}/cancel`);
            if (!ownsScope()) return;
            advance();
            setOperation({ scope, record: data });
            controller.current?.abort();
            refresh();
        } catch (error) {
            if (ownsScope()) setFailure({ scope, error });
        } finally {
            if (ownsScope()) setCancelling(false);
        }
    };
    return { record, active, cancelling, generate, cancel, error: failure?.scope === scope ? failure.error : watch.error };
}
