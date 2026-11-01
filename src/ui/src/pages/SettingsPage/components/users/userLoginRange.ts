/** Parse datetime-local components explicitly and reject DST gaps and repeated times. */
export function localSignInBound(value: string): string | undefined {
    if (!value) return undefined;
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
    if (!match) throw new Error("Invalid local date");
    const [year, month, day, hour, minute] = match.slice(1).map(Number);
    if (year < 1) throw new Error("Invalid local date");
    const date = new Date(0);
    date.setFullYear(year, month - 1, day);
    date.setHours(hour, minute, 0, 0);
    const matches = (candidate: Date) =>
        candidate.getFullYear() === year &&
        candidate.getMonth() === month - 1 &&
        candidate.getDate() === day &&
        candidate.getHours() === hour &&
        candidate.getMinutes() === minute;
    if (!matches(date)) throw new Error("Invalid local date");
    // A repeated wall-clock minute has two UTC instants. Require an unambiguous minute.
    for (let offset = -180; offset <= 180; offset += 15) {
        if (offset && matches(new Date(date.getTime() + offset * 60_000))) throw new Error("Ambiguous local date");
    }
    return date.toISOString();
}
