import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { reminderSelectClass } from "./ReminderRecurrenceFields";

export type RecipientOverrides = { include: string[]; exclude: string[] };
export type ReminderMember = { uid: string; label: string };

type RecipientPage = { items: ReminderMember[]; next_cursor: string | null };

export default function ReminderRecipients({
    path,
    visibility,
    members,
    value,
    onChange,
}: {
    path: string;
    visibility?: string;
    members: ReminderMember[];
    value: RecipientOverrides;
    onChange: (next: RecipientOverrides) => void;
}) {
    const { t } = useTranslation();
    const [expanded, setExpanded] = useState(false);
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const recipients = useInfiniteQuery({
        queryKey: ["reminder-recipient-options", path, visibility, actor, identity],
        enabled: expanded && !!actor,
        initialPageParam: null as string | null,
        queryFn: async ({ pageParam, signal }) =>
            (await api.get<RecipientPage>(path, { signal, params: { cursor: pageParam }, env: { interceptToast: true } as never })).data,
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        retry: false,
        refetchOnWindowFocus: false,
    });
    const selected = new Set([...value.include, ...value.exclude]);
    const eligible = recipients.isError ? [] : (recipients.data?.pages.flatMap((page) => page.items) ?? []);
    const options = eligible.filter((member) => !selected.has(member.uid));
    return (
        <details className="rounded-md border px-3 py-2" onToggle={(event) => setExpanded(event.currentTarget.open)}>
            <summary className="cursor-pointer text-sm font-medium">{t("card.reminder.recipients")}</summary>
            <div className="mt-2 space-y-3">
                {(["include", "exclude"] as const).map((kind) => (
                    <fieldset className="space-y-2" key={kind}>
                        <legend className="text-sm text-muted-foreground">{t(`card.reminder.recipient.${kind}`)}</legend>
                        {value[kind].length > 0 && (
                            <ul className="flex flex-wrap gap-1">
                                {value[kind].map((uid) => {
                                    const label =
                                        eligible.find((member) => member.uid === uid)?.label ??
                                        members.find((member) => member.uid === uid)?.label ??
                                        t("card.reminder.recipient.unavailable");
                                    return (
                                        <li key={uid} className="flex items-center gap-1 rounded-md border ps-2 text-xs">
                                            {label}
                                            <Button
                                                type="button"
                                                size="sm"
                                                variant="ghost"
                                                aria-label={t("card.reminder.recipient.remove", { name: label })}
                                                onClick={() => onChange({ ...value, [kind]: value[kind].filter((item) => item !== uid) })}
                                            >
                                                ×
                                            </Button>
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                        <select
                            className={reminderSelectClass}
                            aria-label={t(`card.reminder.recipient.${kind}`)}
                            value=""
                            disabled={value[kind].length >= 50 || !options.length}
                            onChange={(event) => {
                                const uid = event.target.value;
                                if (uid) onChange({ ...value, [kind]: [...value[kind], uid] });
                            }}
                        >
                            <option value="">{t("card.reminder.recipient.choose")}</option>
                            {options.map((member) => (
                                <option key={member.uid} value={member.uid}>
                                    {member.label}
                                </option>
                            ))}
                        </select>
                    </fieldset>
                ))}
                {recipients.isFetching && (
                    <p role="status" className="text-xs text-muted-foreground">
                        {t("common.Loading...")}
                    </p>
                )}
                {recipients.isError ? (
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void (recipients.isFetchNextPageError ? recipients.fetchNextPage() : recipients.refetch())}
                    >
                        {t("common.Retry")}
                    </Button>
                ) : (
                    recipients.hasNextPage && (
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={recipients.isFetching}
                            onClick={() => void recipients.fetchNextPage()}
                        >
                            {t("notification.inbox.more")}
                        </Button>
                    )
                )}
            </div>
        </details>
    );
}
