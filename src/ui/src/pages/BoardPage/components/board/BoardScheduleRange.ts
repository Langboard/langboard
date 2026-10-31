export const localDate = (date: Date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
export const dayRange = (date: Date) => ({
    start: localDate(date),
    end: localDate(new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1)),
});
export const monthRange = (date: Date) => ({
    start: localDate(new Date(date.getFullYear(), date.getMonth(), 1)),
    end: localDate(new Date(date.getFullYear(), date.getMonth() + 1, 1)),
});
export const parseLocal = (value: string) => {
    const [year, month, day] = value.split("-").map(Number);
    const date = new Date(year, month - 1, day);
    return /^\d{4}-\d{2}-\d{2}$/.test(value) && localDate(date) === value ? date : new Date(NaN);
};
export const validRange = (start: Date, end: Date) =>
    Number.isFinite(start.getTime()) && Number.isFinite(end.getTime()) && end > start && end.getTime() - start.getTime() <= 93 * 86400000;
