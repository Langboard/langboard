import { ThemeProvider } from "next-themes";
import React from "react";
import ReactDOM from "react-dom/client";
import "@/core/injection";
import App from "@/App";
import { setupPreloadRecovery } from "@/core/helpers/setupPreloadRecovery";
import { initializeBrowserTelemetry } from "@/core/helpers/BrowserTelemetry";
import "@/assets/styles/main.css";

const Strict = process.env.IS_PRODUCTION !== "true" ? React.StrictMode : React.Fragment;

setupPreloadRecovery(window);

// Telemetry is optional and must never hold application startup behind its network.
void initializeBrowserTelemetry({
    dsn: process.env.PUBLIC_SENTRY_DSN || "",
    environment: process.env.PUBLIC_SENTRY_ENVIRONMENT || "",
    release: process.env.PUBLIC_SENTRY_RELEASE || "",
}).catch(() => console.warn("Browser telemetry could not be initialized"));

const configuredPublicUIURL = new URL(process.env.PUBLIC_UI_URL || window.location.origin);
const currentURL = new URL(window.location.href);
const loopbackHostnames = new Set(["localhost", "127.0.0.1"]);

if (
    process.env.IS_PRODUCTION === "true" &&
    loopbackHostnames.has(configuredPublicUIURL.hostname) &&
    loopbackHostnames.has(currentURL.hostname) &&
    configuredPublicUIURL.origin !== currentURL.origin
) {
    currentURL.protocol = configuredPublicUIURL.protocol;
    currentURL.hostname = configuredPublicUIURL.hostname;
    currentURL.port = configuredPublicUIURL.port;
    window.location.replace(currentURL);
} else {
    ReactDOM.createRoot(document.getElementById("root")!).render(
        <Strict>
            <ThemeProvider attribute="class">
                <App />
            </ThemeProvider>
        </Strict>
    );
}
