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
        "other editor failure",
        "same content remount",
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
            const otherScopes = ["lb-draft-v1:other-user:project:comment-new:card", "lb-draft-v1:user:project:comment-new:other-card"];
            await page.evaluate((keys) => {
                for (const key of keys) localStorage.setItem(key, JSON.stringify({ content: "Other scope", savedAt: Date.now() }));
            }, otherScopes);
            await editor.fill("Draft A");
            await page.getByRole("button", { name: "common.Save", exact: true }).click();
            await page.getByRole("button", { name: "Inspect draft" }).click();
            const initial = JSON.parse(await page.locator("output").innerText());
            if (outcome.startsWith("newer input") || outcome === "image next submit") await editor.fill("Draft B");
            if (outcome.startsWith("other editor")) await page.getByRole("button", { name: "Switch editor" }).click();
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
            if (outcome === "remount" || outcome === "image remount" || outcome === "same content remount") {
                await page.getByRole("button", { name: "Remount composer" }).click();
                if (mode === "edit") {
                    const edit = page.getByRole("button", { name: "common.Edit", exact: true });
                    if (await edit.count()) await edit.click();
                } else await page.getByRole("button", { name: /card.Add a comment/ }).click();
                await editor.fill(outcome === "same content remount" ? "Draft A" : "Draft B");
            }
            await page.getByRole("button", { name: outcome.includes("failure") ? "Resolve failure" : "Resolve success" }).click();
            await page.getByRole("button", { name: "Inspect draft" }).click();
            const state = JSON.parse(await page.locator("output").innerText());
            expect(state.submitted).toBe("Draft A");
            expect(await page.evaluate((keys) => keys.map((key) => JSON.parse(localStorage.getItem(key)!).content), otherScopes)).toEqual([
                "Other scope",
                "Other scope",
            ]);
            if (outcome === "same content remount") {
                expect(state.draft).toBe("Draft A");
                await expect(editor).toHaveValue("Draft A");
            } else if (outcome === "other editor failure") {
                expect(state.draft).toBe("Draft A");
                expect(state.currentEditor).toBe("other-editor");
            } else if (outcome.startsWith("image")) {
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
            } else if (outcome === "other editor") {
                expect(state.currentEditor).toBe("other-editor");
                expect(state.draft).toBe("");
                if (mode !== "edit") {
                    await page.getByRole("button", { name: "Remount composer" }).click();
                    await page.getByRole("button", { name: /card.Add a comment/ }).click();
                    await expect(editor).toHaveValue("");
                }
            } else {
                expect(state.draft).toBe("");
                expect(state.currentEditor).toBeNull();
            }
        });
    }
}

// Uses real BoardComment with a mocked PlateEditor; verifies candidate props only.
for (const visibility of ["PRIVATE", "INTERNAL", "SHARED"]) {
    test(`comment draft save: edit mention candidates: ${visibility} and reactive changes`, async ({ page }) => {
        page.on("pageerror", (error) => {
            throw error;
        });
        await page.goto(`${baseURL}/?mode=edit&visibility=${visibility}`);
        await page.getByRole("button", { name: "common.Edit", exact: true }).click();
        const editor = page.getByRole("textbox", { name: "Comment content" });
        const candidates = (currentVisibility: string) => JSON.stringify(currentVisibility === "PRIVATE" ? [] : ["user", "other-user", "bot"]);
        await expect(editor).toHaveAttribute("data-mention-candidates", candidates(visibility));
        await expect(editor).toBeEditable();
        await editor.fill("Draft survives visibility changes");
        for (const nextVisibility of ["INTERNAL", "PRIVATE", "SHARED", "PRIVATE"]) {
            await page.getByRole("button", { name: `Set visibility ${nextVisibility}`, exact: true }).click();
            await expect(editor).toHaveAttribute("data-mention-candidates", candidates(nextVisibility));
            await expect(editor).toHaveValue("Draft survives visibility changes");
        }
        await page.getByRole("button", { name: "common.Save", exact: true }).click();
        await page.getByRole("button", { name: "Resolve success", exact: true }).click();
        await page.getByRole("button", { name: "Inspect draft", exact: true }).click();
        const state = JSON.parse(await page.locator("output").innerText());
        expect(state.submitted).toBe("Draft survives visibility changes");
        expect(state.draft).toBe("");
        expect(state.currentEditor).toBeNull();
    });
}

for (const kind of ["matching", "stale", "stale revision", "legacy"]) {
    test(`comment draft save: edit ${kind} draft respects saved baseline`, async ({ page }) => {
        await page.addInitScript((draftKind) => {
            localStorage.setItem(
                "lb-draft-v1:user:project:comment-edit:comment",
                JSON.stringify({
                    content: "Recovered local draft",
                    savedAt: Date.now(),
                    ...(draftKind === "legacy"
                        ? {}
                        : {
                              base_content: draftKind === "matching" || draftKind === "stale revision" ? "Saved original" : "Old saved comment",
                              base_revision: draftKind === "matching" ? "revision-1" : "revision-old",
                              base_updated_at: new Date(0).toISOString(),
                          }),
                })
            );
        }, kind);
        await page.goto(`${baseURL}/?mode=edit`);
        await page.getByRole("button", { name: "common.Edit", exact: true }).click();
        const editor = page.getByRole("textbox", { name: "Comment content" });
        if (kind === "matching") {
            await expect(editor).toHaveValue("Recovered local draft");
            await expect(page.getByRole("alert")).toHaveCount(0);
        } else {
            await expect(editor).toHaveValue("Saved original");
            await expect(editor).toHaveAttribute("readonly", "");
            await expect(page.getByRole("alert")).toBeVisible();
            await page.getByRole("button", { name: "Inspect draft" }).click();
            expect(JSON.parse(await page.locator("output").innerText()).draft).toBe("Recovered local draft");
            await page.getByRole("button", { name: "card.Restore draft", exact: true }).click();
            await expect(editor).toHaveValue("Recovered local draft");
        }
        await page.getByRole("button", { name: "common.Save", exact: true }).click();
        await page.getByRole("button", { name: "Inspect draft" }).click();
        const state = JSON.parse(await page.locator("output").innerText());
        expect(state.submitted).toBe("Recovered local draft");
        expect(state.submittedRevision).toBe("revision-1");
    });
}

test("comment draft save: discard stale draft starts from saved comment", async ({ page }) => {
    await page.addInitScript(() =>
        localStorage.setItem("lb-draft-v1:user:project:comment-edit:comment", JSON.stringify({ content: "Old draft", savedAt: Date.now() }))
    );
    await page.goto(`${baseURL}/?mode=edit`);
    await page.getByRole("button", { name: "common.Edit", exact: true }).click();
    await page.getByRole("button", { name: "card.Discard draft", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Comment content" })).toHaveValue("Saved original");
    await page.getByRole("button", { name: "Inspect draft" }).click();
    expect(JSON.parse(await page.locator("output").innerText()).draft).toBe("");
});

test("comment draft save: remote edit fences save and preserves local draft", async ({ page }) => {
    await page.goto(`${baseURL}/?mode=edit`);
    await page.getByRole("button", { name: "common.Edit", exact: true }).click();
    const editor = page.getByRole("textbox", { name: "Comment content" });
    await editor.fill("Local pending work");
    await page.getByRole("button", { name: "Remote update", exact: true }).click();
    await expect(editor).toHaveValue("Local pending work");
    await expect(page.getByRole("button", { name: "common.Save", exact: true })).toBeDisabled();
    await expect(page.getByRole("alert")).toBeVisible();
    await page.getByRole("button", { name: "card.Show saved comment", exact: true }).click();
    await expect(editor).toHaveValue("Saved remote update");
    await page.getByRole("button", { name: "Inspect draft" }).click();
    const state = JSON.parse(await page.locator("output").innerText());
    expect(state.draft).toBe("Local pending work");
    expect(state.serverContent).toBe("Saved remote update");
    expect(state.submitted).toBe("");
    await page.getByRole("button", { name: "common.Edit", exact: true }).click();
    await expect(editor).toHaveValue("Saved remote update");
    await expect(page.getByRole("button", { name: "card.Restore draft", exact: true })).toBeVisible();
});

test("comment draft save: stale revision response preserves draft and requires reload", async ({ page }) => {
    await page.goto(`${baseURL}/?mode=edit`);
    await page.getByRole("button", { name: "common.Edit", exact: true }).click();
    const editor = page.getByRole("textbox", { name: "Comment content" });
    await editor.fill("Local stale-revision work");
    await page.getByRole("button", { name: "common.Save", exact: true }).click();
    await page.getByRole("button", { name: "Resolve conflict", exact: true }).click();
    await expect(editor).toHaveValue("Local stale-revision work");
    await expect(page.getByRole("button", { name: "common.Save", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "card.Reload saved comment", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Inspect draft" }).click();
    const state = JSON.parse(await page.locator("output").innerText());
    expect(state.draft).toBe("Local stale-revision work");
    expect(state.serverContent).toBe("Saved original");
    expect(state.submittedRevision).toBe("revision-1");
});

test("comment draft save: late success never replaces a newer server projection", async ({ page }) => {
    await page.goto(`${baseURL}/?mode=edit`);
    await page.getByRole("button", { name: "common.Edit", exact: true }).click();
    const editor = page.getByRole("textbox", { name: "Comment content" });
    await editor.fill("Submitted local work");
    await page.getByRole("button", { name: "common.Save", exact: true }).click();
    await page.getByRole("button", { name: "Remote update", exact: true }).click();
    await page.getByRole("button", { name: "Resolve success", exact: true }).click();
    await page.getByRole("button", { name: "Inspect draft" }).click();
    const state = JSON.parse(await page.locator("output").innerText());
    expect(state.serverContent).toBe("Saved remote update");
    expect(state.draft).toBe("Submitted local work");
    await expect(editor).toHaveValue("Submitted local work");
    await expect(page.getByRole("button", { name: "common.Save", exact: true })).toBeDisabled();
});

for (const outcome of ["success", "failure"]) {
    test(`comment draft save: empty newer edit survives late ${outcome} and remount`, async ({ page }) => {
        await page.goto(`${baseURL}/?mode=edit`);
        await page.getByRole("button", { name: "common.Edit", exact: true }).click();
        const editor = page.getByRole("textbox", { name: "Comment content" });
        await editor.fill("Submitted local work");
        await page.getByRole("button", { name: "common.Save", exact: true }).click();
        await editor.fill("");
        await page.getByRole("button", { name: `Resolve ${outcome}`, exact: true }).click();
        const key = "lb-draft-v1:user:project:comment-edit:comment";
        const draft = await page.evaluate((draftKey) => JSON.parse(localStorage.getItem(draftKey) ?? "null"), key);
        expect(draft?.content).toBe("");
        expect(draft?.base_content).toBe(outcome === "success" ? "Submitted local work" : "Saved original");
        await page.getByRole("button", { name: "Remount composer", exact: true }).click();
        await expect(editor).toBeEditable();
        await expect(editor).toHaveValue("");
        await page.reload();
        await page.getByRole("button", { name: "common.Edit", exact: true }).click();
        if (outcome === "success") {
            // This fixture recreates its server model on reload. Recovery must keep
            // the empty draft fenced against that deliberately different baseline.
            await page.getByRole("button", { name: "card.Restore draft", exact: true }).click();
        }
        await expect(editor).toHaveValue("");
    });
}

for (const outcome of ["success", "failure"]) {
    test(`comment draft save: restored unchanged edit survives navigation until ${outcome}`, async ({ page }) => {
        const key = "lb-draft-v1:user:project:comment-edit:comment";
        await page.addInitScript((draftKey) => {
            localStorage.setItem(
                draftKey,
                JSON.stringify({
                    content: "Restored work",
                    base_content: "Saved original",
                    base_revision: "revision-1",
                    base_updated_at: new Date(0).toISOString(),
                    savedAt: Date.now(),
                })
            );
        }, key);
        await page.goto(`${baseURL}/?mode=edit`);
        await page.getByRole("button", { name: "common.Edit", exact: true }).click();
        await expect(page.getByRole("textbox", { name: "Comment content" })).toHaveValue("Restored work");
        await page.getByRole("button", { name: "common.Save", exact: true }).click();
        await page.getByRole("button", { name: "Switch editor", exact: true }).click();
        await page.getByRole("button", { name: `Resolve ${outcome}`, exact: true }).click();
        await page.getByRole("button", { name: "Inspect draft", exact: true }).click();
        const state = JSON.parse(await page.locator("output").innerText());
        expect(state.submitted).toBe("Restored work");
        expect(state.currentEditor).toBe("other-editor");
        expect(state.draft).toBe(outcome === "success" ? "" : "Restored work");
        const persisted = await page.evaluate((draftKey) => JSON.parse(localStorage.getItem(draftKey) ?? "null"), key);
        expect(persisted?.content ?? null).toBe(outcome === "success" ? null : "Restored work");
    });
}
