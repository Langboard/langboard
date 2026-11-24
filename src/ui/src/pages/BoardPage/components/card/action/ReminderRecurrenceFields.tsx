import Input from "@/components/base/Input";
import { useTranslation } from "react-i18next";

export type ReminderRecurrence = {
    interval: number;
    weekdays?: number[];
    month_day?: number | null;
    missing_day?: "skip" | "last";
    nth_weekday?: [number, number] | null;
    last_workday?: boolean;
    until?: string | null;
    count?: number | null;
};

type MonthPattern = "anchor" | "date" | "lastDay" | "ordinal" | "lastWorkday";
const monthPatterns: MonthPattern[] = ["anchor", "date", "lastDay", "ordinal", "lastWorkday"];
const mondayInUTC = Date.UTC(2024, 0, 1);
const dayInMillis = 86_400_000;

export const reminderSelectClass = "select min-h-9 w-full rounded-md border bg-background px-2 text-sm";

export default function ReminderRecurrenceFields({
    frequency,
    value,
    until,
    onUntilChange,
    onChange,
}: {
    frequency: "daily" | "weekly" | "monthly";
    value: ReminderRecurrence;
    until: string;
    onUntilChange: (value: string) => void;
    onChange: (next: ReminderRecurrence) => void;
}) {
    const { t, i18n } = useTranslation();
    const pattern: MonthPattern = value.last_workday
        ? "lastWorkday"
        : value.nth_weekday
          ? "ordinal"
          : value.month_day === 31 && value.missing_day === "last"
            ? "lastDay"
            : value.month_day
              ? "date"
              : "anchor";
    const update = (patch: Partial<ReminderRecurrence>) => onChange({ ...value, ...patch });
    const weekdayFormat = new Intl.DateTimeFormat(i18n.language, { weekday: "short", timeZone: "UTC" });
    const weekdays = Array.from({ length: 7 }, (_, day) => weekdayFormat.format(mondayInUTC + day * dayInMillis));
    return (
        <fieldset className="grid gap-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("card.reminder.repeatDetails")}</legend>
            <label className="grid gap-1 text-sm">
                {t(`card.reminder.interval.${frequency}`)}
                <Input
                    type="number"
                    min={1}
                    step={1}
                    required
                    value={value.interval || ""}
                    onChange={(event) => update({ interval: event.target.valueAsNumber })}
                />
            </label>
            {frequency === "weekly" && (
                <fieldset className="space-y-2">
                    <legend className="text-sm">{t("card.reminder.weekdays")}</legend>
                    <div className="flex flex-wrap gap-x-3 gap-y-2">
                        {weekdays.map((label, day) => (
                            <label key={day} className="flex items-center gap-1.5 text-sm">
                                <input
                                    type="checkbox"
                                    className="checkbox size-4"
                                    checked={value.weekdays?.includes(day) ?? false}
                                    onChange={(event) => {
                                        const next = new Set(value.weekdays ?? []);
                                        if (event.target.checked) next.add(day);
                                        else next.delete(day);
                                        update({ weekdays: [...next].sort((a, b) => a - b) });
                                    }}
                                />
                                {label}
                            </label>
                        ))}
                    </div>
                </fieldset>
            )}
            {frequency === "monthly" && (
                <>
                    <label className="grid gap-1 text-sm">
                        {t("card.reminder.monthPattern.label")}
                        <select
                            className={reminderSelectClass}
                            value={pattern}
                            onChange={(event) => {
                                const next = event.target.value;
                                update({
                                    month_day: next === "lastDay" ? 31 : next === "date" ? 1 : null,
                                    missing_day: next === "lastDay" ? "last" : "skip",
                                    last_workday: next === "lastWorkday",
                                    nth_weekday: next === "ordinal" ? [1, 0] : null,
                                });
                            }}
                        >
                            {monthPatterns.map((part) => (
                                <option key={part} value={part}>
                                    {t(`card.reminder.monthPattern.${part}`)}
                                </option>
                            ))}
                        </select>
                    </label>
                    {pattern === "date" && (
                        <>
                            <label className="grid gap-1 text-sm">
                                {t("card.reminder.monthDay")}
                                <select
                                    className={reminderSelectClass}
                                    value={value.month_day ?? 1}
                                    onChange={(event) => update({ month_day: Number(event.target.value) })}
                                >
                                    {Array.from({ length: 31 }, (_, index) => index + 1).map((day) => (
                                        <option key={day} value={day}>
                                            {day}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label className="flex items-center gap-2 text-sm">
                                <input
                                    className="checkbox size-4"
                                    type="checkbox"
                                    checked={value.missing_day === "last"}
                                    onChange={(event) => update({ missing_day: event.target.checked ? "last" : "skip" })}
                                />
                                {t("card.reminder.missingDayLast")}
                            </label>
                        </>
                    )}
                    {value.nth_weekday && (
                        <div className="grid gap-3 sm:grid-cols-2">
                            <label className="grid gap-1 text-sm">
                                {t("card.reminder.ordinal")}
                                <select
                                    className={reminderSelectClass}
                                    value={value.nth_weekday[0]}
                                    onChange={(event) => update({ nth_weekday: [Number(event.target.value), value.nth_weekday![1]] })}
                                >
                                    {[1, 2, 3, 4, 5].map((number) => (
                                        <option key={number} value={number}>
                                            {number}
                                        </option>
                                    ))}
                                    <option value={-1}>{t("card.reminder.lastOrdinal")}</option>
                                </select>
                            </label>
                            <label className="grid gap-1 text-sm">
                                {t("card.reminder.weekday")}
                                <select
                                    className={reminderSelectClass}
                                    value={value.nth_weekday[1]}
                                    onChange={(event) => update({ nth_weekday: [value.nth_weekday![0], Number(event.target.value)] })}
                                >
                                    {weekdays.map((label, day) => (
                                        <option key={day} value={day}>
                                            {label}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        </div>
                    )}
                </>
            )}
            <label className="grid gap-1 text-sm">
                {t("card.reminder.until")}
                <Input type="datetime-local" value={until} onChange={(event) => onUntilChange(event.target.value)} />
            </label>
            <label className="grid gap-1 text-sm">
                {t("card.reminder.count")}
                <Input
                    type="number"
                    min={1}
                    step={1}
                    value={value.count ?? ""}
                    onChange={(event) => update({ count: event.target.value ? event.target.valueAsNumber : null })}
                />
            </label>
        </fieldset>
    );
}
