const APP_EVENTS = new Set([
    "io.langboard.notification.requested.v1",
    "io.langboard.app.connection.credential.v1",
    "io.langboard.app.transfer.requested.v1",
    "io.langboard.app.execution.requested.v1",
    "io.langboard.app.interaction.answered.v1",
    "io.langboard.app.card.archived.v1",
]);

export function eligibleWebhookOrigin(webhook: { url: string; events?: string[] }, dataOrigins?: string[]): string {
    if (!Array.isArray(webhook.events) || !webhook.events.some((event) => APP_EVENTS.has(event))) return "";
    try {
        const url = new URL(webhook.url);
        if (url.protocol !== "https:" || url.username || url.password || url.hash || webhook.url.includes("#")) return "";
        return dataOrigins?.includes(url.origin) ? url.origin : "";
    } catch {
        return "";
    }
}
