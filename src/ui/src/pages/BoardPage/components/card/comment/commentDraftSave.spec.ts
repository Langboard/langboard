import { test, expect } from "@playwright/test";
import { build } from "esbuild";
import { createServer, type Server } from "node:http";
import { readFile, mkdtemp } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";

const directory = path.resolve("src/pages/BoardPage/components/card/comment");
const fixture = path.join(directory, "commentDraftSave.fixture.tsx");
let server: Server;
let baseURL: string;
test.beforeAll(async () => {
    const temporary = await mkdtemp(path.join(tmpdir(), "comment-draft-save-"));
    const mocks: Record<string, string> = {
        "@/core/providers/BoardCardProvider": "useBoardCard, useBoardCardPanel",
        "@/core/models": "BotModel, ProjectCard",
        "@/core/models/ModelRegistry": "ModelRegistry, isModel",
        "@/core/models/roles": "ProjectRole",
        "react-i18next": "useTranslation",
        "@/components/Editor/plate-editor": "PlateEditor",
        "@/components/ImagePreviewDialog/NativeImageCommentContext": "useNativeImageComments",
        "@/core/hooks/useToggleEditingByClickOutside": "useToggleEditingByClickOutside as default",
        "@/core/helpers/setupApiErrorHandler": "setupApiErrorHandler as default",
        "@/core/utils/ComponentUtils": "cn",
        "@platejs/mention": "getMentionOnSelectItem",
        "@langboard/core/constants": "EEditorType",
        "@/components/base/Form": "Form as default",
        "@/components/base/Drawer": "Drawer as default",
        "@/components/base/Toast": "Toast as default",
        "@/components/UserAvatar": "UserAvatar as default",
    };
    const result = await build({
        entryPoints: [fixture],
        bundle: true,
        write: false,
        format: "esm",
        jsx: "automatic",
        tsconfig: "tsconfig.app.json",
        define: { "process.env.NODE_ENV": JSON.stringify("test") },
        plugins: [
            {
                name: "local-component-boundaries",
                setup(plugin) {
                    plugin.onResolve({ filter: /.*/ }, (args) => {
                        let exports = mocks[args.path];
                        if (/use(Add|Update|Delete)CardComment$/.test(args.path)) exports = "useMutation as default";
                        if (/components\/base\/(Button|SubmitButton)$/.test(args.path)) exports = "Button as default";
                        if (/components\/base\/(Box|Flex)$/.test(args.path)) exports = "Box as default";
                        if (
                            /components\/(UserAvatarDefaultList|UserLikeComponent|DateDistance|base\/(Skeleton|Separator))$/.test(args.path) ||
                            /BoardComment(Seen|Reaction)$/.test(args.path)
                        )
                            exports = "Empty as default";
                        if (/commentAnchor$/.test(args.path)) exports = "resolveCardCommentAnchorElement";
                        if (exports) return { path: exports, namespace: "fixture-mock" };
                        return undefined;
                    });
                    plugin.onLoad({ filter: /.*/, namespace: "fixture-mock" }, (args) => ({
                        contents: `export { ${args.path} } from ${JSON.stringify(fixture)};`,
                        resolveDir: directory,
                    }));
                },
            },
        ],
        absWorkingDir: process.cwd(),
        outdir: temporary,
    });
    const html = await readFile(path.join(directory, "commentDraftSave.fixture.html"));
    server = createServer((request, response) => {
        response.setHeader("Content-Type", request.url === "/fixture.js" ? "text/javascript" : "text/html");
        response.end(request.url === "/fixture.js" ? result.outputFiles[0].contents : html);
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("Missing local fixture address");
    baseURL = `http://127.0.0.1:${address.port}`;
});
test.afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
});
for (const mode of ["panel", "mobile", "edit"]) {
    for (const outcome of [
        "newer input",
        "normal success",
        "failure",
        "newer input failure",
        "other editor",
        "reopen",
        "remount",
        "image next submit",
        "image reopen",
        "image remount",
    ]) {
        if (mode === "edit" && outcome.startsWith("image")) continue;
        test(`comment draft save: ${mode}: ${outcome}`, async ({ page }) => {
            page.on("pageerror", (error) => {
                throw error;
            });
            await page.setViewportSize({ width: mode === "mobile" ? 390 : mode === "edit" ? 900 : 1280, height: 850 });
            await page.goto(`${baseURL}/?mode=${mode}${outcome.startsWith("image") ? "&image" : ""}`);
            if (mode === "edit") await page.getByRole("button", { name: "common.Edit", exact: true }).click();
            else await page.getByRole("button", { name: /card.Add a comment/ }).click();
            const editor = page.getByRole("textbox", { name: "Comment content" });
            await editor.fill("Draft A");
            await page.getByRole("button", { name: "common.Save", exact: true }).click();
            await page.getByRole("button", { name: "Inspect draft" }).click();
            const initial = JSON.parse(await page.locator("output").innerText());
            if (outcome.startsWith("newer input") || outcome === "image next submit") await editor.fill("Draft B");
            if (outcome === "other editor") await page.getByRole("button", { name: "Switch editor" }).click();
            if (outcome === "reopen" || outcome === "image reopen") {
                await page.getByRole("button", { name: "Switch editor" }).click();
                if (mode === "edit") await page.getByRole("button", { name: "common.Edit", exact: true }).click();
                else if (mode === "mobile") await page.getByRole("button", { name: /card.Add a comment/ }).click();
                else {
                    // Re-enter the same editor after another surface took focus.
                    await page.getByRole("button", { name: "Remount composer" }).click();
                    await page.getByRole("button", { name: /card.Add a comment/ }).click();
                }
                await editor.fill("Draft B");
            }
            if (outcome === "remount" || outcome === "image remount") {
                await page.getByRole("button", { name: "Remount composer" }).click();
                if (mode === "edit") {
                    const edit = page.getByRole("button", { name: "common.Edit", exact: true });
                    if (await edit.count()) await edit.click();
                } else await page.getByRole("button", { name: /card.Add a comment/ }).click();
                await editor.fill("Draft B");
            }
            await page.getByRole("button", { name: outcome.includes("failure") ? "Resolve failure" : "Resolve success" }).click();
            await page.getByRole("button", { name: "Inspect draft" }).click();
            const state = JSON.parse(await page.locator("output").innerText());
            expect(state.submitted).toBe("Draft A");
            if (outcome.startsWith("image")) {
                expect(state.draft).toBe("Draft B");
                expect(state.submittedRequestUID).toBe(initial.submittedRequestUID);
                expect(initial.submittedRequestUID).toBeTruthy();
                expect(state.snapshot.anchor.source.attachment_uid).toBe("attachment");
                expect(state.snapshot.request_uid).not.toBe(state.submittedRequestUID);
                await page.getByRole("button", { name: "common.Save", exact: true }).click();
                await page.getByRole("button", { name: "Inspect draft" }).click();
                const next = JSON.parse(await page.locator("output").innerText());
                expect(next.submitted).toBe("Draft B");
                expect(next.submittedRequestUID).toBe(state.snapshot.request_uid);
                await page.getByRole("button", { name: "Resolve success" }).click();
                await page.getByRole("button", { name: "Inspect draft" }).click();
                expect(JSON.parse(await page.locator("output").innerText()).draft).toBe("");
            } else if (outcome.startsWith("newer input") || outcome === "remount" || outcome === "reopen" || outcome === "image reopen") {
                expect(state.draft).toBe("Draft B");
                await expect(editor).toHaveValue("Draft B");
            } else if (outcome === "failure") {
                expect(state.draft).toBe("Draft A");
                await expect(editor).toHaveValue("Draft A");
            } else if (outcome === "other editor") expect(state.currentEditor).toBe("other-editor");
            else {
                expect(state.draft).toBe("");
                expect(state.currentEditor).toBeNull();
            }
        });
    }
}
