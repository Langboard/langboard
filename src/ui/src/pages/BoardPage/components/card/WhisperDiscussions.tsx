import { BOARD_PANE_BEFORE_CLOSE, useBoardSplitPane } from "@/core/providers/BoardSplitProvider";
import Toast from "@/components/base/Toast";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import IconComponent from "@/components/base/IconComponent";
import Select from "@/components/base/Select";
import Textarea from "@/components/base/Textarea";
import { formatDateDistance, formatDateTime } from "@/core/utils/LocaleFormat";
import { api } from "@/core/helpers/Api";
import { isAxiosError } from "axios";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface WhisperAnchor {
    key: string;
    label: string;
    form: { relationship_uid?: string; subanchor_type?: "content_block" | "comment" | "checklist" | "attachment"; subanchor_uid?: string };
}
export interface WhisperThread {
    uid: string;
    state: "OPEN" | "RESOLVED";
    creator_uid: string;
    created_at: string;
    has_unread?: boolean;
    preview?: string;
    anchor: { type: "CARD" | "RELATIONSHIP"; uid: string; subanchor_type: string | null; subanchor_uid: string | null };
}
interface Message {
    uid: string;
    author_uid: string;
    content: string;
    created_at: string;
    mention_uids: string[];
}
interface Participant {
    user_uid: string;
    firstname: string;
    lastname: string;
    last_read_message_uid: string | null;
    has_unread: boolean;
}
interface Props {
    projectUID: string;
    cardUID: string;
    userUID: string;
    anchors: WhisperAnchor[];
    people: { uid: string; name: string; external?: boolean }[];
}
const config = { env: { interceptToast: true } as never };
const queryOptions = { retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false } as const;

export default function WhisperDiscussions(props: Props) {
    return <DiscussionPanel key={`${props.userUID}/${props.projectUID}/${props.cardUID}`} {...props} />;
}
function DiscussionPanel({ projectUID, cardUID, userUID, anchors, people }: Props) {
    const [t, i18n] = useTranslation();
    const failedDraftKey = useRef("new");
    const [open, setOpen] = useState(false);
    const [selected, setSelected] = useState<string | null>(null);
    const [anchor, setAnchor] = useState("card");
    const [drafts, setDrafts] = useState<Record<string, { content: string; mentions: string[] }>>({});
    const [busy, setBusy] = useState(false);
    const [uncertain, setUncertain] = useState(false);
    const [error, setError] = useState(false);
    const [rejected, setRejected] = useState(false);
    const [accessLost, setAccessLost] = useState(false);
    const [threadPages, setThreadPages] = useState<(string | undefined)[]>([undefined]);
    const [messagePages, setMessagePages] = useState<(string | undefined)[]>([undefined]);
    const [showPeople, setShowPeople] = useState(false);
    const [participantPages, setParticipantPages] = useState<(string | undefined)[]>([undefined]);
    const path = `/board/${projectUID}/card/${cardUID}/whispers`;
    const scope = ["human-whispers", userUID, projectUID, cardUID];
    const threads = useQuery({
        ...queryOptions,
        queryKey: [...scope, "threads", threadPages.at(-1)],
        enabled: open,
        queryFn: async ({ signal }) =>
            (
                await api.get<{ threads: WhisperThread[]; next_cursor: string | null }>(path, {
                    ...config,
                    signal,
                    params: { before_uid: threadPages.at(-1) },
                })
            ).data,
    });
    const messages = useQuery({
        ...queryOptions,
        queryKey: [...scope, selected, "messages", messagePages.at(-1)],
        enabled: open && !!selected,
        queryFn: async ({ signal }) =>
            (
                await api.get<{ thread: WhisperThread; messages: Message[]; next_cursor: string | null }>(`${path}/${selected}/messages`, {
                    ...config,
                    signal,
                    params: { before_uid: messagePages.at(-1) },
                })
            ).data,
    });
    const participants = useQuery({
        ...queryOptions,
        queryKey: [...scope, selected, "participants", participantPages.at(-1)],
        enabled: open && !!selected && showPeople,
        queryFn: async ({ signal }) =>
            (
                await api.get<{ participants: Participant[]; next_cursor: string | null }>(`${path}/${selected}/participants`, {
                    ...config,
                    signal,
                    params: { before_uid: participantPages.at(-1) },
                })
            ).data,
    });
    const draftKey = selected ?? "new";
    const draft = drafts[draftKey] ?? { content: "", mentions: [] };
    const setDraft = (value: Partial<typeof draft>) => setDrafts((saved) => ({ ...saved, [draftKey]: { ...draft, ...value } }));
    const paneID = useBoardSplitPane();
    const dirty = Object.values(drafts).some((value) => value.content.trim() || value.mentions.length);
    useEffect(() => {
        if (!dirty) return;
        const warn = (event: BeforeUnloadEvent) => event.preventDefault();
        const beforeClose = (event: Event) => {
            if ((event as CustomEvent<{ paneID: string }>).detail.paneID !== paneID) return;
            event.preventDefault();
            Toast.Add.error(t("card.whispers.unsaved"));
        };
        window.addEventListener("beforeunload", warn);
        window.addEventListener(BOARD_PANE_BEFORE_CLOSE, beforeClose);
        return () => {
            window.removeEventListener("beforeunload", warn);
            window.removeEventListener(BOARD_PANE_BEFORE_CLOSE, beforeClose);
        };
    }, [dirty, paneID, t]);
    const readFailed = threads.isError || (!!selected && messages.isError) || (!!selected && showPeople && participants.isError);
    useEffect(() => {
        if (readFailed) setAccessLost(true);
    }, [readFailed]);
    const unavailable = accessLost || readFailed;
    const loading = threads.isFetching || (!!selected && messages.isFetching);
    const activeThread = selected ? messages.data?.thread : undefined;
    const selectedAnchor = anchors.find((item) => item.key === anchor);
    const canWrite = !busy && !uncertain && !unavailable && !loading && (selected ? activeThread?.state === "OPEN" : !!selectedAnchor);
    const personName = (uid: string) => people.find((person) => person.uid === uid)?.name || uid;
    const anchorName = (thread: WhisperThread) =>
        anchors.find(
            (item) =>
                item.key ===
                (thread.anchor.type === "RELATIONSHIP"
                    ? `relationship:${thread.anchor.uid}`
                    : thread.anchor.subanchor_type
                      ? `${thread.anchor.subanchor_type}:${thread.anchor.subanchor_uid}`
                      : "card")
        )?.label || t("card.whispers.card");
    const refresh = async () => {
        const refreshed = await Promise.all([
            threads.refetch(),
            ...(selected ? [messages.refetch()] : []),
            ...(selected && showPeople ? [participants.refetch()] : []),
        ]);
        if (refreshed.every((result) => !result.isError)) setAccessLost(false);
    };
    const write = async (operation: () => Promise<unknown>, clearDraft = false) => {
        if (busy || uncertain || unavailable) return;
        setBusy(true);
        setError(false);
        setRejected(false);
        try {
            await operation();
            if (clearDraft) setDrafts((saved) => ({ ...saved, [draftKey]: { content: "", mentions: [] } }));
            await refresh();
        } catch (failure) {
            const status = isAxiosError(failure) ? failure.response?.status : undefined;
            if (status === 401 || status === 403 || status === 404) {
                setAccessLost(true);
                return;
            }
            if (status === 400 || status === 422) {
                setRejected(true);
                return;
            }
            failedDraftKey.current = draftKey;
            setError(true);
            setUncertain(true);
        } finally {
            setBusy(false);
        }
    };
    const selectThread = (uid: string | null) => {
        setSelected(uid);
        setMessagePages([undefined]);
        setParticipantPages([undefined]);
        setShowPeople(false);
    };
    return (
        <section className="min-w-0 space-y-3 rounded-lg border p-3" aria-busy={busy || loading} data-card-outline-section="whispers">
            <Button size="sm" variant="ghost" aria-expanded={open} disabled={busy} onClick={() => setOpen(!open)}>
                <IconComponent icon="messages-square" size="4" className="mr-2" />
                {t("card.whispers.title")}
                {dirty && <span className="ml-2 size-1.5 rounded-full bg-primary" aria-label={t("card.whispers.unsaved")} />}
            </Button>
            {open && (
                <>
                    <p className="text-sm text-muted-foreground">{t("card.whispers.help")}</p>
                    <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="outline" disabled={busy || loading} onClick={() => void refresh()}>
                            {t("card.whispers.refresh")}
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => selectThread(null)}>
                            {t("card.whispers.new")}
                        </Button>
                    </div>
                    {loading && (
                        <p role="status" className="text-sm text-muted-foreground">
                            {t("common.Loading...")}
                        </p>
                    )}
                    {unavailable && (
                        <p role="alert" className="text-sm text-muted-foreground">
                            {t("card.whispers.unavailable")}
                        </p>
                    )}
                    {!unavailable && !loading && (
                        <>
                            <div className="space-y-1">
                                {threads.data?.threads.length === 0 && <p className="text-sm text-muted-foreground">{t("card.whispers.empty")}</p>}
                                {threads.data?.threads.map((thread) => (
                                    <Button
                                        key={thread.uid}
                                        variant={selected === thread.uid ? "secondary" : "ghost"}
                                        className="h-auto w-full justify-start whitespace-normal text-left"
                                        disabled={busy}
                                        onClick={() => selectThread(thread.uid)}
                                    >
                                        {thread.has_unread && (
                                            <span className="mr-2 size-1.5 shrink-0 rounded-full bg-primary" aria-label={t("card.whispers.unread")} />
                                        )}
                                        <span className="min-w-0 break-words">
                                            {anchorName(thread)} · {t(`card.whispers.${thread.state}`)} · {personName(thread.creator_uid)}
                                            <time
                                                dateTime={thread.created_at}
                                                title={formatDateTime(new Date(thread.created_at), i18n.language)}
                                                className="ml-2 text-xs text-muted-foreground"
                                            >
                                                {formatDateDistance(new Date(thread.created_at), i18n.language)}
                                            </time>
                                            {thread.preview && (
                                                <span className="mt-1 block text-xs font-normal text-muted-foreground [overflow-wrap:anywhere]">
                                                    {thread.preview}
                                                </span>
                                            )}
                                        </span>
                                    </Button>
                                ))}
                            </div>
                            <Pager pages={threadPages} setPages={setThreadPages} next={threads.data?.next_cursor} disabled={busy} />
                            {selected && activeThread && (
                                <div className="space-y-3 border-t pt-3">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-sm font-medium">
                                            {anchorName(activeThread)} · {t(`card.whispers.${activeThread.state}`)}
                                        </span>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={busy || uncertain}
                                            onClick={() =>
                                                void write(() =>
                                                    api.patch(
                                                        `${path}/${selected}`,
                                                        { state: activeThread.state === "OPEN" ? "RESOLVED" : "OPEN" },
                                                        config
                                                    )
                                                )
                                            }
                                        >
                                            {t(activeThread.state === "OPEN" ? "card.whispers.resolve" : "card.whispers.reopen")}
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={busy}
                                            onClick={() => setShowPeople(!showPeople)}
                                            aria-expanded={showPeople}
                                        >
                                            {t("card.whispers.people")}
                                        </Button>
                                    </div>
                                    <ol className="space-y-3">
                                        {messages.data?.messages.map((message) => (
                                            <li key={message.uid} className="rounded-md bg-muted/40 p-3 text-sm">
                                                <div className="mb-1 text-xs text-muted-foreground">
                                                    {personName(message.author_uid)} ·{" "}
                                                    <time
                                                        dateTime={message.created_at}
                                                        title={formatDateTime(new Date(message.created_at), i18n.language)}
                                                    >
                                                        {formatDateDistance(new Date(message.created_at), i18n.language)}
                                                    </time>
                                                </div>
                                                <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{message.content}</p>
                                                {!!message.mention_uids.length && (
                                                    <p className="mt-2 text-xs text-muted-foreground">
                                                        {message.mention_uids.map((uid) => `@${personName(uid)}`).join(" · ")}
                                                    </p>
                                                )}
                                            </li>
                                        ))}
                                    </ol>
                                    <Pager pages={messagePages} setPages={setMessagePages} next={messages.data?.next_cursor} disabled={busy} />
                                    {!!messages.data?.messages.length && (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={busy || uncertain}
                                            onClick={() =>
                                                void write(() =>
                                                    api.put(`${path}/${selected}/read`, { message_uid: messages.data!.messages.at(-1)!.uid }, config)
                                                )
                                            }
                                        >
                                            {t("card.whispers.markRead")}
                                        </Button>
                                    )}
                                    {showPeople && (
                                        <div className="space-y-2 text-sm">
                                            {participants.isFetching && <p role="status">{t("common.Loading...")}</p>}
                                            {participants.isError && <p role="alert">{t("card.whispers.unavailable")}</p>}
                                            {!participants.isError &&
                                                !participants.isFetching &&
                                                participants.data?.participants.map((person) => (
                                                    <p key={person.user_uid}>
                                                        {person.firstname} {person.lastname} ·{" "}
                                                        {t(person.has_unread ? "card.whispers.unread" : "card.whispers.read")}
                                                    </p>
                                                ))}
                                            <Pager
                                                pages={participantPages}
                                                setPages={setParticipantPages}
                                                next={participants.data?.next_cursor}
                                                disabled={busy}
                                            />
                                        </div>
                                    )}
                                </div>
                            )}
                            {!selected && (
                                <Select.Root value={anchor} onValueChange={setAnchor} disabled={busy || uncertain}>
                                    <Select.Trigger aria-label={t("card.whispers.anchor")}>
                                        <Select.Value />
                                    </Select.Trigger>
                                    <Select.Content>
                                        {anchors.map((item) => (
                                            <Select.Item key={item.key} value={item.key}>
                                                {item.label}
                                            </Select.Item>
                                        ))}
                                    </Select.Content>
                                </Select.Root>
                            )}
                            {(!selected || activeThread?.state === "OPEN") && (
                                <div className="space-y-3">
                                    <Textarea
                                        aria-label={t("card.whispers.message")}
                                        value={draft.content}
                                        maxLength={16000}
                                        disabled={busy || uncertain}
                                        onChange={(event) => setDraft({ content: event.target.value })}
                                        className="min-h-24"
                                    />
                                    {!!draft.mentions.length && (
                                        <p className="text-xs text-muted-foreground">
                                            {draft.mentions.map((uid) => `@${personName(uid)}`).join(" · ")}
                                        </p>
                                    )}
                                    <details>
                                        <summary className="cursor-pointer text-sm text-muted-foreground">{t("card.whispers.mention")}</summary>
                                        <div className="mt-2 max-h-40 space-y-2 overflow-y-auto">
                                            {people
                                                .filter((person) => !person.external)
                                                .map((person) => (
                                                    <Checkbox
                                                        key={person.uid}
                                                        label={person.name}
                                                        checked={draft.mentions.includes(person.uid)}
                                                        disabled={
                                                            busy || uncertain || (!draft.mentions.includes(person.uid) && draft.mentions.length >= 10)
                                                        }
                                                        onCheckedChange={(checked) =>
                                                            setDraft({
                                                                mentions: checked
                                                                    ? [...draft.mentions, person.uid]
                                                                    : draft.mentions.filter((uid) => uid !== person.uid),
                                                            })
                                                        }
                                                    />
                                                ))}
                                        </div>
                                    </details>
                                    <div className="flex flex-wrap gap-2">
                                        <Button
                                            size="sm"
                                            disabled={!canWrite || !draft.content.trim()}
                                            onClick={() =>
                                                void write(async () => {
                                                    const form = { content: draft.content, mention_uids: draft.mentions };
                                                    if (selected) await api.post(`${path}/${selected}/messages`, form, config);
                                                    else {
                                                        const created = await api.post<{ thread: WhisperThread }>(
                                                            path,
                                                            { ...form, ...selectedAnchor!.form },
                                                            config
                                                        );
                                                        selectThread(created.data.thread.uid);
                                                    }
                                                }, true)
                                            }
                                        >
                                            {t(selected ? "card.whispers.reply" : "card.whispers.create")}
                                        </Button>
                                        {selected && !!draft.mentions.length && (
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={!canWrite}
                                                onClick={() =>
                                                    void write(() =>
                                                        api.post(`${path}/${selected}/participants`, { user_uids: draft.mentions }, config)
                                                    )
                                                }
                                            >
                                                {t("card.whispers.invite")}
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                    {rejected && (
                        <p role="alert" className="text-sm">
                            {t("card.whispers.rejected")}
                        </p>
                    )}
                    {error && (
                        <p role="alert" className="text-sm">
                            {t("card.whispers.unknown")}
                        </p>
                    )}
                    {uncertain && (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={busy || loading}
                            onClick={() => {
                                setDrafts((saved) => ({ ...saved, [failedDraftKey.current]: { content: "", mentions: [] } }));
                                setUncertain(false);
                                setError(false);
                            }}
                        >
                            {t("card.whispers.discard")}
                        </Button>
                    )}
                </>
            )}
        </section>
    );
}
function Pager({
    pages,
    setPages,
    next,
    disabled,
}: {
    pages: (string | undefined)[];
    setPages: (pages: (string | undefined)[]) => void;
    next?: string | null;
    disabled: boolean;
}) {
    const [t] = useTranslation();
    if (pages.length === 1 && !next) return null;
    return (
        <div className="flex gap-2">
            <Button size="sm" variant="ghost" disabled={disabled || pages.length === 1} onClick={() => setPages(pages.slice(0, -1))}>
                {t("card.whispers.newer")}
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled || !next} onClick={() => next && setPages([...pages, next])}>
                {t("card.whispers.older")}
            </Button>
        </div>
    );
}
