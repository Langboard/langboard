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
    Sentry.setTag("private_tag", "secret-tag-value");
    Sentry.setTag("transport_code", "secret-scope-value");
    Sentry.setExtra("card_description", "secret-extra-value");
    Sentry.addBreadcrumb({ message: "secret-breadcrumb-value" });
}
document.getElementById("state")!.textContent = "Ready";
document.getElementById("trigger")!.addEventListener("click", () => {
    if (new URLSearchParams(location.search).has("handled")) {
        const invalid = new URLSearchParams(location.search).has("invalid");
        reportBrowserError(
            Object.assign(new Error("secret-handled-transport-value"), {
                code: invalid ? "secret-invalid-code" : "ERR_BAD_RESPONSE",
                response: { status: invalid ? 999 : 503, data: "secret-response-body" },
                config: { url: "/board/private-card?token=secret-config-value" },
            })
        );
        return;
    }
    // Exercise the official SDK's browser handler, not a replaced transport adapter.
    throw new Error("Synthetic confidential card description secret-test-value");
});
