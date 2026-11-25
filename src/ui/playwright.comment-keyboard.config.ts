import { defineConfig } from "@playwright/test";
import base from "./playwright.responsive-comments.config";

// Geometry and local comment submission do not require a collaboration owner.
// The real card, Drawer, and Plate editor remain mounted; API reads/writes use the existing fixture.
export default defineConfig({
    ...base,
    grep: /above the visual keyboard|preserve reply mention/,
    testMatch: ["workbench-shell.spec.ts"],
    outputDir: "../../.verification/mobile-comment-keyboard/results",
    webServer: base.webServer.filter((server) => server.command.includes("vite ")),
});
