import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";
import { canUseReminderPush, subscribeReminderPush, unsubscribeReminderPush } from "../src/controllers/api/notification/reminderBrowserPush.ts";

test("desktop Chrome asks permission before creating a dedicated subscription", async (t) => {
    const restore = new Map();
    const set = (name, value) => {
        restore.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
        Object.defineProperty(globalThis, name, { configurable: true, value });
    };
    t.after(() => {
        for (const [name, descriptor] of restore) {
            if (descriptor) Object.defineProperty(globalThis, name, descriptor);
            else delete globalThis[name];
        }
    });
    const calls = [];
    let permission = "denied";
    const subscription = { unsubscribe: async () => calls.push("unsubscribe"), options: { applicationServerKey: Uint8Array.of(1).buffer } };
    let current = null;
    const registration = {
        scope: "https://langboard.example/notifications",
        getNotifications: async () => [{ close: () => calls.push("close-notification") }],
        pushManager: {
            getSubscription: async () => current,
            subscribe: async (options) => {
                calls.push(["subscribe", options]);
                current = subscription;
                return subscription;
            },
        },
    };
    const browser = {
        userAgent: "Chrome/140.0",
        serviceWorker: {
            register: async (...args) => calls.push(["register", ...args]),
            getRegistration: async (path) => {
                assert.equal(path, "/notifications");
                return registration;
            },
            ready: Promise.resolve(registration),
        },
    };
    set("navigator", browser);
    set("PushManager", class {});
    set("Notification", {
        requestPermission: async () => {
            calls.push("permission");
            return permission;
        },
    });
    assert.equal(canUseReminderPush(), true);
    assert.deepEqual(calls, []);
    await assert.rejects(subscribeReminderPush("AQ"), { name: "NotAllowedError" });
    assert.deepEqual(calls, ["permission"]);
    permission = "granted";
    assert.equal(await subscribeReminderPush("AQ"), subscription);
    assert.deepEqual(calls.slice(1), [
        "permission",
        ["register", "/reminder-push-sw.js", { scope: "/notifications" }],
        ["subscribe", { userVisibleOnly: true, applicationServerKey: "AQ" }],
    ]);
    await subscribeReminderPush("AQ");
    assert.equal(calls.filter((call) => call[0] === "subscribe").length, 1);
    await unsubscribeReminderPush();
    assert.deepEqual(calls.slice(-2), ["close-notification", "unsubscribe"]);
    browser.userAgent = "Chrome/140.0 Mobile";
    assert.equal(canUseReminderPush(), false);
});

test("worker hides the notification body and confines clicks to authorized page entry points", async () => {
    const listeners = {};
    const shown = [];
    const opened = [];
    const self = {
        location: { origin: "https://langboard.example" },
        registration: { showNotification: async (title, options) => shown.push({ title, options }) },
        clients: { matchAll: async () => [], openWindow: async (url) => opened.push(url) },
        addEventListener: (name, run) => (listeners[name] = run),
    };
    const worker = await readFile(new URL("../public/reminder-push-sw.js", import.meta.url), "utf8");
    vm.runInNewContext(worker, { self, URL });
    let work;
    const waitUntil = (promise) => (work = promise);
    listeners.push({
        data: { json: () => ({ version: 1, path: "/board/abc123/card456", title: "private", body: "private text" }) },
        waitUntil,
    });
    await work;
    assert.equal(shown[0].title, "Langboard");
    assert.equal(shown[0].options.body, undefined);
    assert.equal(shown[0].options.data.path, "/board/abc123/card456");
    let closed = false;
    listeners.notificationclick({
        notification: { data: shown[0].options.data, close: () => (closed = true) },
        waitUntil,
    });
    await work;
    assert.equal(closed, true);
    listeners.notificationclick({
        notification: { data: { path: "//external.example" }, close() {} },
        waitUntil,
    });
    await work;
    assert.deepEqual(opened, ["https://langboard.example/board/abc123/card456", "https://langboard.example/notifications"]);
});

test("concurrent urgent reminders use one summary and ignore a duplicate event", async () => {
    const listeners = {};
    const shown = [];
    const self = {
        registration: {
            getNotifications: async ({ tag }) =>
                shown.filter(({ options }) => options.tag === tag).slice(-1).map(({ options }) => ({ data: options.data })),
            showNotification: async (title, options) => shown.push({ title, options }),
        },
        addEventListener: (name, run) => (listeners[name] = run),
    };
    const worker = await readFile(new URL("../public/reminder-push-sw.js", import.meta.url), "utf8");
    vm.runInNewContext(worker, { self, URL });
    const tasks = [];
    for (const event_uid of ["firstEvent", "secondEvent", "firstEvent"]) {
        listeners.push({
            data: { json: () => ({ version: 1, path: "/board/one/two", preview: "Card title", urgent: true, event_uid }) },
            waitUntil: (promise) => tasks.push(promise),
        });
    }
    await Promise.all(tasks);
    assert.equal(shown.length, 2);
    assert.equal(shown[0].options.body, "Card title");
    assert.equal(shown[0].options.tag, shown[1].options.tag);
    assert.equal(shown[1].title, "Langboard · 2");
    assert.equal(shown[1].options.body, undefined);
    assert.equal(shown[1].options.data.path, "/notifications");
});
