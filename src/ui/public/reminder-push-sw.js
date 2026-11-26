const inboxPath = "/notifications";
const cardPath = /^\/board\/[A-Za-z0-9]+\/[A-Za-z0-9]+$/;
const urgentTag = "langboard-urgent-reminders";
const urgentWindowMs = 60_000;
let urgentQueue = Promise.resolve();

function reminderTarget(value) {
    return typeof value === "string" && cardPath.test(value) ? value : inboxPath;
}

async function showUrgent(options, eventUID) {
    const [previous] = await self.registration.getNotifications({ tag: urgentTag });
    const now = Date.now();
    const data = previous?.data;
    const ids = data && now - data.startedAt < urgentWindowMs ? data.ids ?? [] : [];
    if (ids.includes(eventUID)) return;
    const count = ids.length + 1;
    await self.registration.showNotification(count > 1 ? `Langboard · ${count}` : "Langboard", {
        tag: urgentTag,
        ...(count === 1 && options.body ? { body: options.body } : {}),
        data: { path: count > 1 ? inboxPath : options.data.path, ids: [...ids, eventUID], startedAt: ids.length ? data.startedAt : now },
    });
}

self.addEventListener("push", (event) => {
    let payload;
    try {
        payload = event.data?.json();
    } catch {
        payload = undefined;
    }
    const valid = payload?.version === 1;
    const path = reminderTarget(valid ? payload.path : undefined);
    const options = { data: { path } };
    if (valid && typeof payload.preview === "string") options.body = payload.preview.slice(0, 120);
    if (valid && payload.urgent === true && typeof payload.event_uid === "string" && /^[A-Za-z0-9]{1,32}$/.test(payload.event_uid)) {
        urgentQueue = urgentQueue.catch(() => {}).then(() => showUrgent(options, payload.event_uid));
        event.waitUntil(urgentQueue);
    } else {
        event.waitUntil(self.registration.showNotification("Langboard", options));
    }
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const target = new URL(reminderTarget(event.notification.data?.path), self.location.origin).href;
    event.waitUntil(
        (async () => {
            const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
            const existing = windows.find((client) => client.url === target);
            if (existing) await existing.focus();
            else await self.clients.openWindow(target);
        })(),
    );
});
