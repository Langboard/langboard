import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/core/helpers/Api";

type Preview = {
    group_uid: string;
    group_name: string;
    snapshot: string;
    target_count: number;
    member_count: number;
    counts: Record<string, number>;
    members: Record<string, { uid: string; name: string }[]>;
};
type Group = { uid: string; display_name: string };

export default function BoardGroupInvitation({ projectUID }: { projectUID: string }) {
    const [t] = useTranslation();
    const [query, setQuery] = useState("");
    const [groups, setGroups] = useState<Group[]>([]);
    const [groupUID, setGroupUID] = useState("");
    const [preview, setPreview] = useState<Preview>();
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [messageKey, setMessageKey] = useState("");
    const requestID = useRef("");
    const epoch = useRef(0);
    const invalidate = () => {
        epoch.current++;
        setPreview(undefined);
        setConfirmed(false);
        requestID.current = "";
        setMessageKey("");
    };
    useEffect(() => {
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            api.get("/settings/groups", { params: { query, limit: 100 }, signal: controller.signal })
                .then((response) => setGroups(response.data.records))
                .catch(() => {
                    if (!controller.signal.aborted) setMessageKey("board.groupInvite.Unavailable");
                });
        }, 250);
        return () => {
            controller.abort();
            window.clearTimeout(timer);
        };
    }, [query]);
    useEffect(() => {
        invalidate();
        setGroupUID("");
    }, [projectUID]);
    const loadPreview = async () => {
        invalidate();
        const current = epoch.current;
        setBusy(true);
        try {
            const response = await api.get(`/board/${projectUID}/members/group-preview`, { params: { group_uid: groupUID } });
            if (current !== epoch.current) return;
            setPreview(response.data);
            requestID.current = crypto.randomUUID();
        } catch {
            if (current === epoch.current) setMessageKey("board.groupInvite.Unavailable");
        } finally {
            setBusy(false);
        }
    };
    const apply = async () => {
        if (!preview || !confirmed || busy) return;
        setBusy(true);
        try {
            await api.post(`/board/${projectUID}/members/group-add`, {
                group_uid: preview.group_uid,
                snapshot: preview.snapshot,
                request_id: requestID.current,
                confirmed_count: preview.target_count,
                confirmed: true,
            });
            invalidate();
            setMessageKey("board.groupInvite.Applied");
        } catch (error) {
            const status = (error as { response?: { status?: number } }).response?.status;
            if (status === 409 || status === 403 || status === 404) {
                invalidate();
                setMessageKey("board.groupInvite.Refresh");
            } else setMessageKey("board.groupInvite.Unavailable");
        } finally {
            setBusy(false);
        }
    };
    return (
        <details
            className="min-w-0 max-w-full rounded border p-2 text-sm"
            onToggle={(event) => {
                if (!event.currentTarget.open) invalidate();
            }}
        >
            <summary>{t("board.groupInvite.Canonical groups")}</summary>
            <div className="mt-2 flex min-w-0 flex-col gap-2">
                <p>{t("board.groupInvite.Personal address books remain in the member picker")}</p>
                <label>
                    {t("board.groupInvite.Search groups")}
                    <input
                        className="w-full rounded border p-2"
                        maxLength={200}
                        value={query}
                        disabled={busy}
                        onChange={(event) => {
                            setQuery(event.target.value);
                            setGroupUID("");
                            invalidate();
                        }}
                    />
                </label>
                <label>
                    {t("board.groupInvite.Canonical group")}
                    <select
                        aria-label={t("board.groupInvite.Canonical group")}
                        className="w-full min-w-0 rounded border p-2"
                        value={groupUID}
                        disabled={busy}
                        onChange={(event) => {
                            setGroupUID(event.target.value);
                            invalidate();
                        }}
                    >
                        <option value="">{t("board.groupInvite.Select group")}</option>
                        {groups.map((group) => (
                            <option key={group.uid} value={group.uid}>
                                {group.display_name} ({group.uid})
                            </option>
                        ))}
                    </select>
                </label>
                <button className="rounded border p-2 disabled:opacity-50" disabled={!groupUID || busy} onClick={loadPreview}>
                    {t("board.groupInvite.Preview")}
                </button>
                {preview && (
                    <div className="min-w-0 break-words" aria-live="polite">
                        <p>{t("board.groupInvite.Target count", { count: preview.target_count })}</p>
                        {Object.entries(preview.counts).map(([kind, count]) => (
                            <details key={kind}>
                                <summary>
                                    {t(`board.groupInvite.${kind}`)}: {count}
                                </summary>
                                <ul>
                                    {preview.members[kind].map((member) => (
                                        <li key={member.uid}>{member.name || member.uid}</li>
                                    ))}
                                </ul>
                            </details>
                        ))}
                        <label className="my-2 flex items-start gap-2">
                            <input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />
                            {t("board.groupInvite.Confirm count", { count: preview.target_count })}
                        </label>
                        <button
                            className="w-full rounded border p-2 disabled:opacity-50"
                            disabled={!confirmed || busy || preview.target_count === 0}
                            onClick={apply}
                        >
                            {t("board.groupInvite.Invite count", { count: preview.target_count })}
                        </button>
                    </div>
                )}
                <p role="status">{messageKey && t(messageKey)}</p>
            </div>
        </details>
    );
}
