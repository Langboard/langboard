const workerPath = "/reminder-push-sw.js";
const scope = "/notifications";

export function canUseReminderPush(): boolean {
    return (
        typeof navigator !== "undefined" &&
        "serviceWorker" in navigator &&
        typeof Notification !== "undefined" &&
        typeof PushManager !== "undefined" &&
        /Chrome\//.test(navigator.userAgent) &&
        !/Android|Mobile|Edg\//.test(navigator.userAgent)
    );
}

export async function subscribeReminderPush(applicationServerKey: string): Promise<PushSubscription> {
    // Invoke permission while the click still owns user activation.
    if ((await Notification.requestPermission()) !== "granted") throw new DOMException("Permission not granted", "NotAllowedError");
    await navigator.serviceWorker.register(workerPath, { scope });
    const registration = await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    if (existing) {
        const configuredKey = Uint8Array.from(atob(applicationServerKey.replace(/-/g, "+").replace(/_/g, "/")), (char) => char.charCodeAt(0));
        const currentKey = new Uint8Array(existing.options.applicationServerKey ?? new ArrayBuffer(0));
        if (currentKey.length === configuredKey.length && currentKey.every((byte, index) => byte === configuredKey[index])) return existing;
        await existing.unsubscribe();
    }
    return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
}

export async function unsubscribeReminderPush() {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    const registration = await navigator.serviceWorker.getRegistration(scope);
    if (!registration || new URL(registration.scope).pathname !== scope) return;
    for (const notification of await registration.getNotifications()) notification.close();
    await (await registration.pushManager.getSubscription())?.unsubscribe();
}
