import { defineConfig } from "@playwright/test";
import base from "./playwright.card-context.config";

export default defineConfig({
    ...base,
    testDir: "./src",
    testMatch: ["workbench-shell.spec.ts", "commentDraftSave.spec.ts"],
    grep: /responsive native comments|comment draft save/,
});
