import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import { ProjectRole } from "@/core/models/roles";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReminderRecurrenceFields, { type ReminderRecurrence, reminderSelectClass } from "./ReminderRecurrenceFields";

import ReminderRecipients, { type RecipientOverrides, type ReminderMember } from "./ReminderRecipients";
import { resolveReminderWallTime, toReminderWallTime } from "./reminderWallTime";

const frequencies = ["once", "daily", "weekly", "monthly"] as const;
const deadlineOffsets = { hour: "-PT1H", day: "-P1D", week: "-P7D" } as const;
type Frequency = (typeof frequencies)[number];
type Schedule = ReminderRecurrence & {
    timezone: string;
    anchor: string | null;
    deadline_offset: string | null;
    frequency: Frequency;
};
type Rule = {
    uid: string;
    revision: number;
    shared: boolean;
    enabled: boolean;
    next_due_at: string | null;
    next_regular_due_at: string | null;
    schedule: Schedule;
    recipient_overrides?: RecipientOverrides | null;
    external_delivery?: boolean;
};
type Page = { items: Rule[]; next_cursor: string | null };
type Command =
    | { exception: Rule; movedTo?: string }
    | ({ schedule: Schedule; recipient_overrides?: RecipientOverrides; external_delivery?: boolean } & (
          | { rule: Rule; enabled: boolean }
          | { request_uid: string; shared: boolean }
      ));

function formatRuleTime(value: string, timezone: string, language: string) {
    return (
        new Intl.DateTimeFormat(language, { dateStyle: "medium", timeStyle: "short", timeZone: timezone }).format(new Date(value)) + ` (${timezone})`
    );
}

export default function BoardCardActionReminder({ buttonClassName }: { buttonClassName: string }) {
    const { t, i18n } = useTranslation();
    const { card, projectUID, currentUser, hasRoleAction } = useBoardCard();
    const isAdmin = currentUser.useField("is_admin");
    const deadline = card.useField("deadline_at");
    const visibility = card.useField("visibility");
    const projectMembers = card.useForeignFieldArray("project_members");
    const members = useMemo(
        () =>
            projectMembers
                .filter((member) => member.isValidUser())
                .map((member) => ({
                    uid: member.uid,
                    label: [member.firstname, member.lastname].filter(Boolean).join(" ") || member.username,
                })),
        [projectMembers]
    );
    const [open, setOpen] = useState(false);
    const [editing, setEditing] = useState<Rule | null>(null);
    const [movingUID, setMovingUID] = useState<string | null>(null);
    const [formGeneration, setFormGeneration] = useState(0);
    const client = useQueryClient();
    const canEditShared = isAdmin || hasRoleAction(ProjectRole.EAction.CardUpdate);
    const path = `/board/${projectUID}/card/${card.uid}/reminders`;
    const queryKey = ["card-reminders", currentUser.uid, projectUID, card.uid];
    const rules = useInfiniteQuery({
        queryKey,
        enabled: open,
        initialPageParam: null as string | null,
        queryFn: async ({ pageParam, signal }) => (await api.get<Page>(path, { signal, params: { cursor: pageParam } })).data,
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        retry: false,
    });
    const save = useMutation({
        retry: false,
        mutationFn: async (command: Command) => {
            if (currentUser.uid !== useAuthStore.getState().currentUser?.uid) throw new Error("Reminder session changed");
            if ("exception" in command) {
                await api.post(`${path}/${command.exception.uid}/next-exception`, {
                    expected_revision: command.exception.revision,
                    expected_due_at: command.exception.next_regular_due_at,
                    moved_to: command.movedTo,
                });
            } else if ("rule" in command) {
                await api.put(`${path}/${command.rule.uid}`, {
                    schedule: command.schedule,
                    enabled: command.enabled,
                    expected_revision: command.rule.revision,
                    recipient_overrides: command.recipient_overrides,
                    external_delivery: command.external_delivery,
                });
            } else {
                await api.post(path, command);
            }
        },
        onSuccess: (_result, command) => {
            if ("request_uid" in command) setFormGeneration((value) => value + 1);
            setEditing(null);
            setMovingUID(null);
        },
        onSettled: () => Promise.all([client.invalidateQueries({ queryKey }), client.invalidateQueries({ queryKey: [`get-cards-${projectUID}`] })]),
    });
    const items = rules.data?.pages.flatMap((page) => page.items) ?? [];

    return (
        <Dialog.Root
            open={open}
            onOpenChange={(value) => {
                setOpen(value);
                setEditing(null);
                setMovingUID(null);
                save.reset();
            }}
        >
            <Dialog.Trigger asChild>
                <Button type="button" variant="secondary" className={buttonClassName}>
                    <IconComponent icon="bell" size="4" />
                    {t("card.reminder.title")}
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto">
                <Dialog.Title>{t("card.reminder.title")}</Dialog.Title>
                <Dialog.Description>{t("card.reminder.description")}</Dialog.Description>
                {rules.isPending && <p role="status">{t("common.Loading...")}</p>}
                {rules.isError && (
                    <div role="alert" className="flex items-center gap-2">
                        {t("card.reminder.loadError")}
                        <Button type="button" variant="outline" size="sm" onClick={() => void rules.refetch()}>
                            {t("common.Retry")}
                        </Button>
                    </div>
                )}
                {!!items.length && (
                    <ul className="divide-y rounded-md border">
                        {items.map((rule) => (
                            <li key={rule.uid} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                                <div className="min-w-0">
                                    <p className="text-sm font-medium">
                                        {t(rule.shared ? "card.reminder.shared" : "card.reminder.personal")} ·{" "}
                                        {t(`card.reminder.${rule.schedule.frequency}`)}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        {rule.next_due_at
                                            ? formatRuleTime(rule.next_due_at, rule.schedule.timezone, i18n.language)
                                            : t(rule.enabled ? "card.reminder.noUpcoming" : "card.reminder.paused")}
                                    </p>
                                    {rule.next_regular_due_at && rule.next_regular_due_at !== rule.next_due_at && (
                                        <p className="text-xs text-muted-foreground">
                                            {t("card.reminder.nextRegularAt", {
                                                time: formatRuleTime(rule.next_regular_due_at, rule.schedule.timezone, i18n.language),
                                            })}
                                        </p>
                                    )}
                                </div>
                                {(!rule.shared || canEditShared) && (
                                    <div className="flex flex-wrap gap-1">
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant="ghost"
                                            disabled={save.isPending}
                                            onClick={() => {
                                                save.reset();
                                                setEditing(rule);
                                            }}
                                        >
                                            {t("card.reminder.edit")}
                                        </Button>
                                        {rule.enabled && rule.next_regular_due_at && rule.schedule.frequency !== "once" && (
                                            <>
                                                <Button
                                                    type="button"
                                                    size="sm"
                                                    variant="ghost"
                                                    disabled={save.isPending}
                                                    onClick={() => save.mutate({ exception: rule })}
                                                    title={toReminderWallTime(rule.next_regular_due_at, rule.schedule.timezone)}
                                                >
                                                    {t("card.reminder.skipNext")}
                                                </Button>
                                                <Button
                                                    type="button"
                                                    size="sm"
                                                    variant="ghost"
                                                    disabled={save.isPending}
                                                    onClick={() => {
                                                        save.reset();
                                                        setMovingUID(rule.uid);
                                                    }}
                                                >
                                                    {t("card.reminder.moveNext")}
                                                </Button>
                                            </>
                                        )}
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant="outline"
                                            disabled={save.isPending}
                                            onClick={() => save.mutate({ rule, enabled: !rule.enabled, schedule: rule.schedule })}
                                        >
                                            {t(rule.enabled ? "card.reminder.pause" : "card.reminder.resume")}
                                        </Button>
                                    </div>
                                )}
                                {movingUID === rule.uid && rule.next_regular_due_at && (!rule.shared || canEditShared) && (
                                    <MoveNextReminder
                                        key={`${rule.uid}/${rule.revision}`}
                                        dueAt={rule.next_regular_due_at}
                                        timezone={rule.schedule.timezone}
                                        busy={save.isPending}
                                        onCancel={() => setMovingUID(null)}
                                        onSave={(movedTo) => save.mutate({ exception: rule, movedTo })}
                                    />
                                )}
                            </li>
                        ))}
                    </ul>
                )}
                {rules.hasNextPage && (
                    <Button type="button" variant="outline" size="sm" disabled={rules.isFetching} onClick={() => void rules.fetchNextPage()}>
                        {t("notification.inbox.more")}
                    </Button>
                )}
                {rules.isSuccess && (
                    <ReminderForm
                        key={editing?.uid ?? formGeneration}
                        editing={editing}
                        hasDeadline={!!deadline}
                        canEditShared={!!canEditShared}
                        canDeliverExternally={visibility !== "PRIVATE"}
                        members={visibility === "PRIVATE" ? [] : members}
                        recipientPath={`${path}/recipient-options`}
                        visibility={visibility}
                        busy={save.isPending}
                        onCancel={() => setEditing(null)}
                        onSave={(command) => save.mutate(command)}
                    />
                )}
                {save.isError && (
                    <p role="alert" className="text-sm text-destructive">
                        {t("card.reminder.saveError")}
                    </p>
                )}
            </Dialog.Content>
        </Dialog.Root>
    );
}

function MoveNextReminder({
    dueAt,
    timezone,
    busy,
    onCancel,
    onSave,
}: {
    dueAt: string;
    timezone: string;
    busy: boolean;
    onCancel: () => void;
    onSave: (instant: string) => void;
}) {
    const { t } = useTranslation();
    const original = toReminderWallTime(dueAt, timezone);
    const [value, setValue] = useState(original);
    const [invalid, setInvalid] = useState(false);
    const submit = (event: FormEvent) => {
        event.preventDefault();
        const instant = resolveReminderWallTime(value, timezone);
        if (!instant) {
            setInvalid(true);
            return;
        }
        onSave(instant);
    };
    return (
        <form className="grid w-full gap-2 rounded-md border p-2" onSubmit={submit}>
            <label className="grid gap-1 text-sm">
                {t("card.reminder.moveNextTime")} ({timezone})
                <Input
                    type="datetime-local"
                    value={value}
                    onChange={(event) => {
                        setValue(event.target.value);
                        setInvalid(false);
                    }}
                    required
                />
            </label>
            {invalid && (
                <p role="alert" className="text-sm text-destructive">
                    {t("card.reminder.invalidWallTime")}
                </p>
            )}
            <div className="flex flex-wrap gap-2">
                <Button type="submit" size="sm" disabled={busy || value === original}>
                    {t("card.reminder.save")}
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
                    {t("card.Cancel")}
                </Button>
            </div>
        </form>
    );
}

function ReminderForm({
    editing,
    hasDeadline,
    canEditShared,
    canDeliverExternally,
    members,
    recipientPath,
    visibility,
    busy,
    onCancel,
    onSave,
}: {
    editing: Rule | null;
    hasDeadline: boolean;
    canEditShared: boolean;
    canDeliverExternally: boolean;
    members: ReminderMember[];
    recipientPath: string;
    visibility?: string;
    busy: boolean;
    onCancel: () => void;
    onSave: (command: Command) => void;
}) {
    const { t } = useTranslation();
    const saved = editing?.schedule;
    const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const initialTimezone = saved?.timezone ?? localTimezone;
    const [timezone, setTimezone] = useState(initialTimezone);
    const [when, setWhen] = useState(() => (saved?.anchor ? toReminderWallTime(saved.anchor, initialTimezone) : ""));
    const [untilWallTime, setUntilWallTime] = useState(() => (saved?.until ? toReminderWallTime(saved.until, initialTimezone) : ""));
    const [invalidWallTime, setInvalidWallTime] = useState(false);
    const [recurrence, setRecurrence] = useState<ReminderRecurrence>(saved ?? { interval: 1 });
    const [frequency, setFrequency] = useState<Frequency>(saved?.frequency ?? "once");
    const [preset, setPreset] = useState<keyof typeof deadlineOffsets | "">("");
    const [shared, setShared] = useState(false);
    const [sendExternally, setSendExternally] = useState(editing?.external_delivery ?? false);
    const [overrides, setOverrides] = useState<RecipientOverrides>(editing?.recipient_overrides ?? { include: [], exclude: [] });
    const showRecipients = visibility !== "PRIVATE" && canEditShared && (editing ? editing.shared : shared);
    const previous = useRef<{ fingerprint: string; uid: string } | null>(null);
    const offset = preset ? deadlineOffsets[preset] : (saved?.deadline_offset ?? null);
    const zoneOptions = useMemo(
        () =>
            [...new Set([localTimezone, "UTC", initialTimezone, ...(Intl.supportedValuesOf?.("timeZone") ?? [])])].map((zone) => (
                <option key={zone} value={zone}>
                    {zone}
                </option>
            )),
        [initialTimezone, localTimezone]
    );
    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (busy || (!offset && !when)) return;
        const sameZone = timezone === initialTimezone;
        const anchor = offset ? null : resolveReminderWallTime(when, timezone, sameZone ? saved?.anchor : null);
        const until = frequency === "once" ? null : resolveReminderWallTime(untilWallTime, timezone, sameZone ? saved?.until : null);
        if ((!offset && !anchor) || (frequency !== "once" && untilWallTime && !until)) {
            setInvalidWallTime(true);
            return;
        }
        setInvalidWallTime(false);
        const schedule: Schedule = {
            ...recurrence,
            anchor,
            deadline_offset: offset,
            frequency,
            timezone,
            interval: frequency === "once" ? 1 : recurrence.interval,
            weekdays: frequency === "weekly" ? recurrence.weekdays : undefined,
            month_day: frequency === "monthly" ? recurrence.month_day : null,
            missing_day: frequency === "monthly" ? recurrence.missing_day : undefined,
            nth_weekday: frequency === "monthly" ? recurrence.nth_weekday : null,
            last_workday: frequency === "monthly" && !!recurrence.last_workday,
            until,
            count: frequency === "once" ? null : recurrence.count,
        };
        const recipient_overrides = showRecipients ? overrides : undefined;
        const external_delivery = canDeliverExternally && sendExternally;
        if (editing) {
            onSave({ rule: editing, enabled: editing.enabled, schedule, recipient_overrides, external_delivery });
            return;
        }
        const fingerprint = JSON.stringify([schedule, shared, recipient_overrides, external_delivery]);
        if (previous.current?.fingerprint !== fingerprint) previous.current = { fingerprint, uid: crypto.randomUUID() };
        onSave({ schedule, request_uid: previous.current.uid, shared, recipient_overrides, external_delivery });
    };

    return (
        <form onSubmit={submit} onChange={() => setInvalidWallTime(false)} className="grid gap-3 rounded-md border p-3">
            <p className="text-sm font-medium">{t(editing ? "card.reminder.edit" : "card.reminder.add")}</p>
            {!editing && hasDeadline && (
                <label className="grid gap-1 text-sm">
                    {t("card.reminder.time")}
                    <select className={reminderSelectClass} value={preset} onChange={(event) => setPreset(event.target.value as typeof preset)}>
                        <option value="">{t("card.reminder.specificTime")}</option>
                        {Object.keys(deadlineOffsets).map((key) => (
                            <option key={key} value={key}>
                                {t(`card.reminder.${key}`)}
                            </option>
                        ))}
                    </select>
                </label>
            )}
            <label className="grid gap-1 text-sm">
                {t("card.reminder.timezone")}
                <select className={reminderSelectClass} value={timezone} onChange={(event) => setTimezone(event.target.value)}>
                    {zoneOptions}
                </select>
            </label>
            {!offset && (
                <label className="grid gap-1 text-sm">
                    {t("card.reminder.specificTime")}
                    <Input type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} required />
                </label>
            )}
            <label className="grid gap-1 text-sm">
                {t("card.reminder.repeat")}
                <select className={reminderSelectClass} value={frequency} onChange={(event) => setFrequency(event.target.value as Frequency)}>
                    {frequencies.map((value) => (
                        <option key={value} value={value}>
                            {t(`card.reminder.${value}`)}
                        </option>
                    ))}
                </select>
            </label>
            {frequency !== "once" && (
                <ReminderRecurrenceFields
                    frequency={frequency}
                    value={recurrence}
                    until={untilWallTime}
                    onUntilChange={setUntilWallTime}
                    onChange={setRecurrence}
                />
            )}
            {!editing && canEditShared && (
                <label className="flex items-center gap-2 text-sm">
                    <input className="checkbox size-4" type="checkbox" checked={shared} onChange={(event) => setShared(event.target.checked)} />
                    {t("card.reminder.shared")}
                </label>
            )}
            {canDeliverExternally && (
                <label className="flex items-center gap-2 text-sm">
                    <input
                        className="checkbox size-4"
                        type="checkbox"
                        checked={sendExternally}
                        onChange={(event) => setSendExternally(event.target.checked)}
                    />
                    {t("card.reminder.external")}
                </label>
            )}
            {showRecipients && (
                <ReminderRecipients path={recipientPath} visibility={visibility} members={members} value={overrides} onChange={setOverrides} />
            )}
            {invalidWallTime && (
                <p role="alert" className="text-sm text-destructive">
                    {t("card.reminder.invalidWallTime")}
                </p>
            )}
            <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={busy}>
                    {t("card.reminder.save")}
                </Button>
                {editing && (
                    <Button type="button" size="sm" variant="outline" onClick={onCancel}>
                        {t("card.Cancel")}
                    </Button>
                )}
            </div>
        </form>
    );
}
