import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Textarea from "@/components/base/Textarea";
import { api } from "@/core/helpers/Api";
import { WORKBENCH_OUTLINE_EVENT } from "@/pages/DashboardPage/components/WorkbenchCommands";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Localized = Record<string, string>;
type Value = { choice_id: string } | { choice_ids: string[] } | { text: string };
interface Question {
    id: string;
    prompt: Localized;
    type: "single_choice" | "multi_choice" | "free_text" | "choice_or_text";
    choices?: { id: string; label: Localized }[];
    min_selections?: number;
    max_selections?: number;
    max_length?: number;
}
export interface CardInteraction {
    interaction_uid: string;
    revision: number;
    state: "pending" | "answered" | "expired" | "cancelled";
    request: {
        kind: "approval" | "question" | "action";
        prompt: Localized;
        questions: Question[];
        expires_at: string;
        execution?: { request_uid: string; generation: number };
        presentation: { type: "card_action" | "choice_card" | "comment_reaction"; comment_uid?: string; reactions?: Record<string, string> };
    };
    response: { values: Record<string, Value>; actor_uid: string } | null;
    delivery_state?: string;
    reception_state?: string;
}
interface Props {
    projectUID: string;
    cardUID: string;
    userUID: string;
    canRespond: boolean;
}
const config = { env: { interceptToast: true } as never };
export default function CardAppInteractions(props: Props) {
    return <InteractionList key={`${props.userUID}/${props.projectUID}/${props.cardUID}`} {...props} />;
}
function InteractionList({ projectUID, cardUID, userUID, canRespond }: Props) {
    const [t] = useTranslation();
    const [open, setOpen] = useState(false);
    const [sending, setSending] = useState(false);
    const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
    const cursor = cursors[cursors.length - 1];
    const path = `/board/${projectUID}/card/${cardUID}/apps/interactions`;
    const query = useQuery({
        queryKey: ["card-interactions", projectUID, cardUID, userUID, cursor],
        queryFn: async ({ signal }) =>
            (
                await api.get<{ items: CardInteraction[]; next_cursor: string | null }>(path, {
                    ...config,
                    signal,
                    params: { cursor },
                })
            ).data,
        enabled: open,
        retry: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    return (
        <section className="space-y-3 rounded-lg border p-3" aria-busy={query.isFetching}>
            <Button size="sm" variant="ghost" aria-expanded={open} disabled={sending} onClick={() => setOpen(!open)}>
                {t("card.appInteractions.title")}
            </Button>
            {open && (
                <>
                    <p className="text-sm text-muted-foreground">{t("card.appInteractions.help")}</p>
                    <Button size="sm" variant="outline" disabled={query.isFetching || sending} onClick={() => void query.refetch()}>
                        {t("card.appInteractions.refresh")}
                    </Button>
                    {query.isFetching && <p role="status">{t("common.Loading...")}</p>}
                    {query.isError && <p role="alert">{t("card.appInteractions.unavailable")}</p>}
                    {!query.isError && query.data && (
                        <>
                            {query.data.items.length === 0 && <p className="text-sm text-muted-foreground">{t("card.appInteractions.empty")}</p>}
                            {query.data.items.map((item) => (
                                <InteractionForm
                                    key={`${item.interaction_uid}/${item.revision}/${query.dataUpdatedAt}`}
                                    item={item}
                                    path={path}
                                    canRespond={canRespond && !query.isFetching && !sending}
                                    onBusy={setSending}
                                    refresh={() => query.refetch()}
                                />
                            ))}
                            <div className="flex flex-wrap gap-2">
                                {cursors.length > 1 && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={query.isFetching || sending}
                                        onClick={() => setCursors(cursors.slice(0, -1))}
                                    >
                                        {t("card.appInteractions.previous")}
                                    </Button>
                                )}
                                {query.data.next_cursor && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={query.isFetching || sending}
                                        onClick={() => setCursors([...cursors, query.data!.next_cursor!])}
                                    >
                                        {t("card.appInteractions.next")}
                                    </Button>
                                )}
                            </div>
                        </>
                    )}
                </>
            )}
        </section>
    );
}
function InteractionForm({
    item,
    path,
    canRespond,
    refresh,
    onBusy,
}: {
    item: CardInteraction;
    path: string;
    canRespond: boolean;
    refresh: () => Promise<{ isError: boolean }>;
    onBusy: (busy: boolean) => void;
}) {
    const [t, i18n] = useTranslation();
    const localized = (value: Localized) => value[i18n.resolvedLanguage ?? i18n.language] ?? value[i18n.language.split("-")[0]] ?? value.en;
    const [values, setValues] = useState<Record<string, Value>>(() =>
        Object.fromEntries(
            item.request.questions.filter((question) => question.type === "multi_choice").map((question) => [question.id, { choice_ids: [] }])
        )
    );
    const [pending, setPending] = useState(false);
    const [uncertain, setUncertain] = useState(false);
    const writing = useRef(false);
    const key = useRef<string | null>(null);
    const [, tick] = useState(0);
    useEffect(() => {
        if (item.state !== "pending") return;
        const timer = setTimeout(() => tick((value) => value + 1), Math.max(0, Date.parse(item.request.expires_at) - Date.now()) + 1);
        return () => clearTimeout(timer);
    }, [item.state, item.request.expires_at]);
    const reaction = item.request.presentation.type === "comment_reaction";
    const state = item.state === "pending" && Date.parse(item.request.expires_at) <= Date.now() ? "expired" : item.state;
    const editable = canRespond && state === "pending" && !reaction && !pending && !uncertain;
    const valid = item.request.questions.every((question) => {
        const value = values[question.id];
        if (!value) return false;
        if ("text" in value) return value.text.trim().length > 0 && value.text.length <= (question.max_length ?? 0);
        if ("choice_ids" in value)
            return (
                value.choice_ids.length >= (question.min_selections ?? 0) &&
                value.choice_ids.length <= (question.max_selections ?? question.choices?.length ?? 0)
            );
        return question.choices?.some((choice) => choice.id === value.choice_id);
    });
    const submit = async () => {
        if (!editable || !valid || writing.current) return;
        writing.current = true;
        key.current ??= crypto.randomUUID();
        setPending(true);
        onBusy(true);
        try {
            await api.post(
                `${path}/${item.interaction_uid}/responses`,
                {
                    expected_revision: item.revision,
                    idempotency_key: key.current,
                    values,
                    ...(item.request.execution ? { execution_generation: item.request.execution.generation } : {}),
                },
                config
            );
            setUncertain(true);
            await refresh();
        } catch {
            // A timeout may follow a committed answer. Read back before any replay.
            setUncertain(true);
        } finally {
            setPending(false);
            writing.current = false;
            onBusy(false);
        }
    };
    return (
        <article className="card card-border card-sm border-border" aria-busy={pending}>
            <div className="card-body min-w-0 p-3">
                <h3 className="card-title break-words text-sm">{localized(item.request.prompt)}</h3>
                <p className="text-xs text-muted-foreground">{t(`card.appInteractions.${state}`)}</p>
                {reaction && (
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                            window.dispatchEvent(
                                new CustomEvent(WORKBENCH_OUTLINE_EVENT, {
                                    detail: { cardUID: path.split("/")[4], section: "comments", commentUID: item.request.presentation.comment_uid },
                                })
                            )
                        }
                    >
                        {t("card.appInteractions.openComment")}
                    </Button>
                )}
                {reaction && (
                    <p className="break-words text-sm">
                        {t("card.appInteractions.reactionHelp")}{" "}
                        {Object.entries(item.request.presentation.reactions ?? {})
                            .map(([name, choiceID]) => {
                                const choice = item.request.questions
                                    .flatMap((question) => question.choices ?? [])
                                    .find((choice) => choice.id === choiceID);
                                return `${t(`reaction.${name}`)} → ${choice ? localized(choice.label) : choiceID}`;
                            })
                            .join(" · ")}
                    </p>
                )}
                {item.response ? (
                    <>
                        {item.request.questions.map((question) => {
                            const value = item.response!.values[question.id];
                            const ids = value && "choice_ids" in value ? value.choice_ids : value && "choice_id" in value ? [value.choice_id] : [];
                            const answer =
                                value && "text" in value
                                    ? value.text
                                    : ids
                                          .map((id) => {
                                              const choice = question.choices?.find((choice) => choice.id === id);
                                              return choice ? localized(choice.label) : id;
                                          })
                                          .join(", ");
                            return (
                                <p key={question.id} className="whitespace-pre-wrap break-words text-sm">
                                    {localized(question.prompt)}: {answer}
                                </p>
                            );
                        })}
                        <p className="text-xs text-muted-foreground">{t("card.appInteractions.acceptedHelp")}</p>
                    </>
                ) : (
                    !reaction && (
                        <form
                            onSubmit={(event) => {
                                event.preventDefault();
                                void submit();
                            }}
                            className="space-y-3"
                        >
                            {item.request.questions.map((question) => {
                                const value = values[question.id];
                                return (
                                    <fieldset key={question.id} className="fieldset min-w-0" disabled={!editable}>
                                        <legend className="fieldset-legend break-words">{localized(question.prompt)}</legend>
                                        {(question.choices ?? []).map((choice) =>
                                            question.type === "multi_choice" ? (
                                                <Checkbox
                                                    key={choice.id}
                                                    label={localized(choice.label)}
                                                    checked={!!value && "choice_ids" in value && value.choice_ids.includes(choice.id)}
                                                    onCheckedChange={(checked) => {
                                                        const ids = value && "choice_ids" in value ? value.choice_ids : [];
                                                        setValues({
                                                            ...values,
                                                            [question.id]: {
                                                                choice_ids: checked ? [...ids, choice.id] : ids.filter((id) => id !== choice.id),
                                                            },
                                                        });
                                                    }}
                                                />
                                            ) : (
                                                <label key={choice.id} className="flex items-start gap-2 text-sm">
                                                    <input
                                                        type="radio"
                                                        name={`${item.interaction_uid}/${question.id}`}
                                                        className="mt-1 accent-current"
                                                        checked={!!value && "choice_id" in value && value.choice_id === choice.id}
                                                        onChange={() => setValues({ ...values, [question.id]: { choice_id: choice.id } })}
                                                    />
                                                    <span className="break-words">{localized(choice.label)}</span>
                                                </label>
                                            )
                                        )}
                                        {(question.type === "free_text" || question.type === "choice_or_text") && (
                                            <label className="block space-y-1">
                                                <span className="text-sm">{t("card.appInteractions.textAnswer")}</span>
                                                <Textarea
                                                    maxLength={question.max_length}
                                                    value={value && "text" in value ? value.text : ""}
                                                    onChange={(event) => setValues({ ...values, [question.id]: { text: event.target.value } })}
                                                />
                                            </label>
                                        )}
                                    </fieldset>
                                );
                            })}
                            {uncertain && (
                                <p role="alert" className="text-sm">
                                    {t("card.appInteractions.readback")}
                                </p>
                            )}
                            {state === "pending" && (
                                <Button type="submit" size="sm" disabled={!editable || !valid}>
                                    {t("card.appInteractions.submit")}
                                </Button>
                            )}
                        </form>
                    )
                )}
            </div>
        </article>
    );
}
