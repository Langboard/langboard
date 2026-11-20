import { defineConfig } from "@playwright/test";
import draftCloseConfig from "./playwright.card-draft-close.config";
import { fullEditServer } from "./playwright.full-edit-server";

export default defineConfig({
    ...draftCloseConfig,
    testMatch: ["fullEditSave.spec.ts"],
    use: {
        ...draftCloseConfig.use,
        baseURL: "http://127.0.0.1:4297",
    },
    webServer: fullEditServer,
});
