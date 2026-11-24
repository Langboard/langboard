import { TZDate } from "react-day-picker";

const minute = 60_000;
const day = 24 * 60 * minute;

export function toReminderWallTime(instant: string, timezone: string): string {
    const date = new TZDate(instant, timezone);
    const year = String(date.getFullYear()).padStart(4, "0");
    const fields = [date.getMonth() + 1, date.getDate(), date.getHours(), date.getMinutes()].map((value) => String(value).padStart(2, "0"));
    return `${year}-${fields[0]}-${fields[1]}T${fields[2]}:${fields[3]}`;
}

/** Interpret a picker value in the rule's zone. A fold uses the earlier instant, like the scheduler. */
export function resolveReminderWallTime(value: string, timezone: string, unchanged?: string | null): string | null {
    if (!value) return null;
    if (unchanged && toReminderWallTime(unchanged, timezone) === value) return unchanged;
    const local = Date.parse(`${value}Z`);
    if (!Number.isFinite(local)) return null;

    const offsets = [-day, 0, day].map((delta) => new TZDate(local + delta, timezone).getTimezoneOffset());
    const matching = offsets
        .map((offset) => local + offset * minute)
        .filter((instant) => Number.isFinite(instant) && toReminderWallTime(new Date(instant).toISOString(), timezone) === value);
    return matching.length ? new Date(Math.min(...matching)).toISOString() : null;
}
