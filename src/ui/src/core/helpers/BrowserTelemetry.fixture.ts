import { initializeBrowserTelemetry, reportBrowserError } from "./BrowserTelemetry";

const disabled = new URLSearchParams(location.search).has("disabled");
await initializeBrowserTelemetry({
    dsn: disabled ? "" : "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@errors.example.test/3",
    environment: "canary",
    release: "test-exact-revision",
});
if (!disabled) {
    const Sentry = await import("@sentry/browser");
    Sentry.setUser({ email: "confidential@example.test" });
    Sentry.setExtra("card_description", "secret-extra-value");
    Sentry.addBreadcrumb({ message: "secret-breadcrumb-value" });
}
document.getElementById("state")!.textContent = "Ready";
document.getElementById("trigger")!.addEventListener("click", () => {
    if (new URLSearchParams(location.search).has("handled")) {
        reportBrowserError(new Error("secret-handled-transport-value"));
        return;
    }
    // Exercise the official SDK's browser handler, not a replaced transport adapter.
    throw new Error("Synthetic confidential card description secret-test-value");
});
