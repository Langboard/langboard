const inboxPath = "/notifications";
const cardPath = /^\/board\/[A-Za-z0-9]+\/[A-Za-z0-9]+$/;

function reminderTarget(value) {
    return typeof value === "string" && cardPath.test(value) ? value : inboxPath;
}

self.addEventListener("push", (event) => {
    let payload;
    try {
        payload = event.data?.json();
    } catch {
        payload = undefined;
    }
    const path = reminderTarget(payload?.version === 1 ? payload.path : undefined);
    event.waitUntil(self.registration.showNotification("Langboard", { data: { path } }));
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
        })()
    );
});
