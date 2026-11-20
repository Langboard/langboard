import type { TFunction } from "i18next";

const states = new Set(["pending", "dispatching", "accepted", "failed", "uncertain", "cancelled"]);
const reasons = new Set([
    "retryable_failure",
    "retry_limit_exceeded",
    "retry_window_expired",
    "permanent_failure",
    "admission_uncertain",
    "dispatch_lease_expired",
    "graph_admission_reserved",
    "scope_excluded",
]);

export function scheduleAdmissionStatus(t: TFunction, state: unknown): string {
    return t(`bot.schedules.admission.states.${typeof state === "string" && states.has(state) ? state : "unknown"}`);
}

export function scheduleAdmissionReason(t: TFunction, reason: unknown): string {
    return t(`bot.schedules.admission.errors.${typeof reason === "string" && reasons.has(reason) ? reason : "unknown_failure"}`);
}
