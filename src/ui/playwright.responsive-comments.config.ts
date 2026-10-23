import { defineConfig } from "@playwright/test";
import base from "./playwright.card-context.config";

export default defineConfig({
    ...base,
    testMatch: "workbench-shell.spec.ts",
    grep: /responsive native comments/,
});
