import { test, expect } from "@playwright/test";
for (const width of [1280, 390])
    test(`template column editor retains failed draft and sends structured columns at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        await page.route("**/settings/global-labels", (route) =>
            route.fulfill({ json: { labels: [{ uid: "global", name: "Question", description: "Ask", color: "#8B5CF6", translations: {} }] } })
        );
        await page.route("**/settings/project-template-bots", (route) =>
            route.fulfill({
                json: { bots: [{ uid: "bot", bot_type: "project_chat", display_name: "Support assistant" }] },
            })
        );
        let fail = true;
        let writes = 0;
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        await page.route("**/settings/project-templates**", async (route) => {
            if (route.request().method() === "GET")
                return route.fulfill({
                    json: {
                        templates: [
                            { uid: "one", name: "Custom", columns: ["Queue"], column_descriptions: ["Legacy"], is_builtin: false, is_default: true },
                        ],
                    },
                });
            writes++;
            const body = route.request().postDataJSON();
            expect(body.description).toBe("Reusable support board");
            expect(body.internal_bot_uids).toEqual(["bot"]);
            expect(body.global_label_uids).toEqual(["global"]);
            expect(body.columns[0]).toMatchObject({
                name: "Queue",
                description: "Edited guidance",
                workflow_stage: null,
                translations: { ko: { name: "대기", description: "" } },
            });
            if (fail) {
                fail = false;
                return route.fulfill({ status: 500, json: {} });
            }
            await route.fulfill({
                json: {
                    template: {
                        ...body,
                        uid: "one",
                        columns: body.columns.map((x: { name: string }) => x.name),
                        column_definitions: body.columns,
                        is_default: true,
                    },
                },
            });
        });
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await expect(page.getByLabel("Column name", { exact: true })).toHaveValue("Queue");
        await page.getByLabel("Column description", { exact: true }).fill("Edited guidance");
        await page.getByLabel("Board template description", { exact: true }).fill("Reusable support board");
        await page.getByRole("checkbox", { name: "Question" }).check();
        await page.getByRole("combobox", { name: "Project chat", exact: true }).selectOption("bot");
        await page.getByRole("button", { name: "ko", exact: true }).click();
        await page.getByLabel("Column name", { exact: true }).fill("대기");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByLabel("Column name", { exact: true })).toHaveValue("대기");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.locator("form")).toHaveCount(0);
        expect(writes).toBe(2);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

test("failed bot choices preserve saved selection during unrelated edits", async ({ page }) => {
    await page.route("**/settings/project-template-bots", (route) => route.fulfill({ status: 500, json: {} }));
    await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
    await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
    let saved = false;
    await page.route("**/settings/project-templates**", (route) => {
        if (route.request().method() === "GET")
            return route.fulfill({
                json: {
                    templates: [
                        {
                            uid: "one",
                            name: "Custom",
                            columns: ["Queue"],
                            is_default: true,
                            internal_bot_selections: [{ internal_bot_uid: "existing", bot_type: "project_chat" }],
                        },
                    ],
                },
            });
        const body = route.request().postDataJSON();
        expect(body).not.toHaveProperty("internal_bot_uids");
        expect(body.description).toBe("Unrelated change");
        saved = true;
        return route.fulfill({ json: { template: { ...body, uid: "one", columns: ["Queue"] } } });
    });
    await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Project chat", exact: true })).toBeDisabled();
    await expect(page.getByRole("combobox", { name: "Project chat", exact: true })).toHaveValue("existing");
    await page.getByLabel("Board template description", { exact: true }).fill("Unrelated change");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator("form")).toHaveCount(0);
    expect(saved).toBe(true);
});

for (const action of ["replace", "clear", "unrelated"] as const)
    test(`template draft bot roles remain owned by edited template after list selection: ${action}`, async ({ page }) => {
        await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        await page.route("**/settings/project-template-bots", (route) =>
            route.fulfill({ json: { bots: [{ uid: "replacement", bot_type: "project_chat", display_name: "Replacement" }] } })
        );
        const templates = [
            {
                uid: "a",
                name: "Template A",
                columns: ["Queue A"],
                is_default: true,
                internal_bot_selections: [
                    { internal_bot_uid: "old-a", bot_type: "project_chat" },
                    { internal_bot_uid: "historical-editor-a", bot_type: "editor_chat" },
                ],
            },
            {
                uid: "b",
                name: "Template B",
                columns: ["Queue B"],
                is_default: false,
                internal_bot_selections: [{ internal_bot_uid: "old-b", bot_type: "project_chat" }],
            },
        ];
        const writes: { url: string; body: { description: string; internal_bot_uids?: string[] } }[] = [];
        await page.route("**/settings/project-templates**", (route) => {
            if (route.request().method() === "GET") return route.fulfill({ json: { templates } });
            const body = route.request().postDataJSON();
            writes.push({ url: route.request().url(), body });
            return route.fulfill({ json: { template: { ...templates[0], ...body } } });
        });
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await page.getByLabel("Board template description", { exact: true }).fill("Unsaved A description");
        await page.getByLabel("Column name", { exact: true }).fill("Unsaved A queue");
        await page.getByRole("combobox").first().click();
        await page.getByRole("option", { name: "Template B · Queue B", exact: true }).click();
        await expect(page.getByRole("combobox").first()).toHaveText("Template B · Queue B");
        await expect(page.getByLabel("Board template name", { exact: true })).toHaveValue("Template A");
        await expect(page.getByLabel("Column name", { exact: true })).toHaveValue("Unsaved A queue");
        if (action !== "unrelated") {
            const chat = page.getByRole("combobox", { name: "Project chat", exact: true });
            await chat.selectOption(action === "replace" ? "replacement" : "");
            if (action === "replace") {
                await chat.selectOption("");
                await chat.selectOption("replacement");
            }
        }
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.locator("form")).toHaveCount(0);
        expect(writes).toHaveLength(1);
        expect(writes[0].url).toMatch(/\/project-templates\/a$/);
        expect(writes[0].body.description).toBe("Unsaved A description");
        if (action === "unrelated") expect(writes[0].body).not.toHaveProperty("internal_bot_uids");
        else
            expect(writes[0].body.internal_bot_uids).toEqual(action === "replace" ? ["historical-editor-a", "replacement"] : ["historical-editor-a"]);
    });

for (const width of [1280, 390])
    test(`long template menus stay within the viewport and remain selectable at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        const name = "Client-Delivery " + "Long template name ".repeat(8);
        const columns = ["Intake", "Scoping", "Proposal", "Approved", "Delivery", "Client Review", "Completed"];
        await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
        await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        await page.route("**/settings/project-templates**", (route) =>
            route.fulfill({
                json: {
                    templates: [
                        { uid: "long", name, columns, is_default: true, is_builtin: false },
                        { uid: "short", name: "Short", columns: ["Queue"], is_default: false, is_builtin: false },
                    ],
                },
            })
        );
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page.getByRole("combobox").click();
        const menu = page.getByRole("listbox");
        await expect(menu).toBeVisible();
        const bounds = await menu.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        await page.getByRole("option", { name: "Short · Queue", exact: true }).click();
        await expect(page.getByRole("combobox")).toHaveText("Short · Queue");
        await page.getByRole("combobox").click();
        await page.getByRole("option", { name: name + " · " + columns.join(" → "), exact: true }).click();
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await expect(page.getByLabel("Board template name", { exact: true })).toHaveValue(name);
    });

for (const width of [1920, 390])
    test(`template loading failure offers a bounded retry without writes at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
        await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        let reads = 0;
        let writes = 0;
        let release!: () => void;
        const held = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route("**/settings/project-templates**", async (route) => {
            if (route.request().method() !== "GET") {
                writes++;
                return route.fulfill({ status: 500, json: {} });
            }
            reads++;
            if (reads === 1) return route.fulfill({ status: 500, json: {} });
            await held;
            await route.fulfill({ json: { templates: [{ uid: "one", name: "Recovered", columns: ["Queue"], is_default: true }] } });
        });
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByRole("button", { name: "New board template", exact: true })).toBeDisabled();
        await expect(page.getByRole("button", { name: "Save default", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.getByRole("status")).toBeVisible();
        await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
        release();
        await expect(page.getByRole("alert")).toHaveCount(0);
        await expect(page.getByRole("status")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeEnabled();
        await expect(page.getByRole("combobox").first()).toContainText("Recovered");
        expect(reads).toBe(2);
        expect(writes).toBe(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

for (const width of [1920, 390])
    test(`English remains the canonical template language at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
        await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        await page.route("**/settings/project-templates**", (route) =>
            route.fulfill({ json: { templates: [{ uid: "one", name: "Custom", columns: ["Queue"], is_default: true }] } })
        );
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await page.getByLabel("Language code", { exact: true }).fill("en");
        await expect(page.getByRole("button", { name: "Add language", exact: true })).toBeDisabled();
        await page.getByLabel("Language code", { exact: true }).fill("fr");
        await page.getByRole("button", { name: "Add language", exact: true }).click();
        await page.getByLabel("Column name", { exact: true }).fill("Accueil");
        await page.getByRole("button", { name: "en", exact: true }).click();
        await expect(page.getByLabel("Column name", { exact: true })).toHaveValue("Queue");
        await page.getByRole("button", { name: "fr", exact: true }).click();
        await expect(page.getByLabel("Column name", { exact: true })).toHaveValue("Accueil");
    });

for (const width of [1920, 390])
    test(`translations follow their columns through save and reread at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        type Column = {
            name: string;
            description: string;
            workflow_stage: string | null;
            translations: Record<string, { name: string; description: string }>;
        };
        let columns: Column[] = [
            { name: "Intake", description: "English guidance", workflow_stage: null, translations: {} },
            { name: "Review", description: "Review guidance", workflow_stage: null, translations: {} },
        ];
        const template = () => ({ uid: "one", name: "Delivery", columns: columns.map((c) => c.name), column_definitions: columns, is_default: true });
        await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
        await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        let writes = 0;
        await page.route("**/settings/project-templates**", (route) => {
            if (route.request().method() === "GET") return route.fulfill({ json: { templates: [template()] } });
            writes++;
            columns = route.request().postDataJSON().columns;
            expect(columns.map((c) => c.name)).toEqual(["Review", "Intake"]);
            expect(columns[1]).toMatchObject({
                description: "English guidance",
                translations: {
                    ko: { name: "접수", description: "한국어 안내" },
                    ja: { name: "受付", description: "日本語案内" },
                    zh: { name: "接收", description: "中文说明" },
                },
            });
            return route.fulfill({ json: { template: template() } });
        });
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        for (const [code, name, description] of [
            ["ko", "접수", "한국어 안내"],
            ["ja", "受付", "日本語案内"],
            ["zh", "接收", "中文说明"],
        ]) {
            await page.getByRole("button", { name: code, exact: true }).click();
            await page.getByLabel("Column name", { exact: true }).nth(0).fill(name);
            await page.getByLabel("Column description", { exact: true }).nth(0).fill(description);
        }
        await page.getByRole("button", { name: "Move up", exact: true }).nth(1).click();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.locator("form")).toHaveCount(0);
        await page.reload();
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await expect(page.getByLabel("Column name", { exact: true }).nth(1)).toHaveValue("Intake");
        for (const [code, name, description] of [
            ["ko", "접수", "한국어 안내"],
            ["ja", "受付", "日本語案内"],
            ["zh", "接收", "中文说明"],
        ]) {
            await page.getByRole("button", { name: code, exact: true }).click();
            await expect(page.getByLabel("Column name", { exact: true }).nth(1)).toHaveValue(name);
            await expect(page.getByLabel("Column description", { exact: true }).nth(1)).toHaveValue(description);
        }
        expect(writes).toBe(1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

test("language limit considers every column and canonical English", async ({ page }) => {
    const translations = Object.fromEntries(
        Array.from({ length: 29 }, (_, index) => [
            "x" + String.fromCharCode(97 + Math.floor(index / 26)) + String.fromCharCode(97 + (index % 26)),
            { name: "Translated", description: "" },
        ])
    );
    await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
    await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
    await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
    await page.route("**/settings/project-templates**", (route) =>
        route.fulfill({
            json: {
                templates: [
                    {
                        uid: "one",
                        name: "Languages",
                        columns: ["Queue", "Review"],
                        is_default: true,
                        column_definitions: [
                            { name: "Queue", description: "", translations: {} },
                            { name: "Review", description: "", translations },
                        ],
                    },
                ],
            },
        })
    );
    await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByLabel("Language code", { exact: true }).fill("fr");
    await expect(page.getByRole("button", { name: "Add language", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Move up", exact: true }).nth(1).click();
    await expect(page.getByRole("button", { name: "Add language", exact: true })).toBeDisabled();
});

test("template save owns one request while duplicate submit events are pending", async ({ page }) => {
    await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
    await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
    await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
    let writes = 0;
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/project-templates**", async (route) => {
        if (route.request().method() === "GET")
            return route.fulfill({
                json: {
                    templates: [
                        {
                            uid: "one",
                            name: "Custom",
                            columns: ["Queue"],
                            is_default: true,
                        },
                    ],
                },
            });
        writes++;
        await pending;
        await route.fulfill({ status: 500, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator("form").evaluate((form) => {
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    try {
        await expect.poll(() => writes).toBe(1);
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    } finally {
        release();
    }
    await expect(page.getByRole("alert")).toBeVisible();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => writes).toBe(2);
});

test("default template selection rejects simultaneous duplicate clicks and allows retry", async ({ page }) => {
    await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
    await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
    await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
    let writes = 0;
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/project-templates**", async (route) => {
        if (route.request().method() === "GET")
            return route.fulfill({
                json: {
                    templates: [
                        {
                            uid: "one",
                            name: "Custom",
                            columns: ["Queue"],
                            is_default: true,
                        },
                    ],
                },
            });
        writes++;
        expect(route.request().url()).toContain("/project-templates/default");
        await pending;
        await route.fulfill({ status: 500, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
    const save = page.getByRole("button", { name: "Save default", exact: true });
    await expect(save).toBeEnabled();
    await save.evaluate((button) => {
        button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    try {
        await expect.poll(() => writes).toBe(1);
        await expect(save).toBeDisabled();
    } finally {
        release();
    }
    await expect(save).toBeEnabled();
    await save.click();
    await expect.poll(() => writes).toBe(2);
});

for (const width of [1280, 390])
    test(`failed template choices recover without losing edits at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        let bots = 0;
        let labels = 0;
        let writes = 0;
        await page.route("**/settings/project-template-bots", (route) => {
            bots++;
            return bots === 1
                ? route.fulfill({ status: 503, json: {} })
                : route.fulfill({ json: { bots: [{ uid: "bot", bot_type: "project_chat", display_name: "Assistant" }] } });
        });
        await page.route("**/settings/global-labels", (route) => {
            labels++;
            return labels === 1
                ? route.fulfill({ status: 503, json: {} })
                : route.fulfill({ json: { labels: [{ uid: "global", name: "Question", description: "Ask", color: "#8B5CF6", translations: {} }] } });
        });
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        await page.route("**/settings/project-templates**", (route) => {
            if (route.request().method() !== "GET") writes++;
            return route.fulfill({
                json: {
                    templates: [
                        {
                            uid: "one",
                            name: "Custom",
                            columns: ["Queue"],
                            global_label_uids: ["global"],
                            is_default: true,
                            internal_bot_selections: [{ internal_bot_uid: "bot", bot_type: "project_chat" }],
                        },
                    ],
                },
            });
        });
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await page.getByLabel("Board template description", { exact: true }).fill("Unsaved retained description");
        await page.getByRole("button", { name: "Default bots · Retry", exact: true }).click();
        await expect(page.getByRole("combobox", { name: "Project chat", exact: true })).toBeEnabled();
        await expect(page.getByRole("combobox", { name: "Project chat", exact: true })).toHaveValue("bot");
        await page.getByRole("button", { name: "Default labels · Retry", exact: true }).click();
        await expect(page.getByRole("checkbox", { name: "Question" })).toBeChecked();
        await expect(page.getByLabel("Board template description", { exact: true })).toHaveValue("Unsaved retained description");
        expect(bots).toBe(2);
        expect(labels).toBe(2);
        expect(writes).toBe(0);
    });

for (const scenario of ["edit", "create", "new session", "reopen session", "failed new session", "failed reopened session"]) {
    test(`delayed template save preserves ${scenario}`, async ({ page }) => {
        await page.route("**/settings/global-labels", (route) => route.fulfill({ json: { labels: [] } }));
        await page.route("**/settings/project-template-bots", (route) => route.fulfill({ json: { bots: [] } }));
        await page.route("**/settings/workflow-stages", (route) => route.fulfill({ json: { stages: [] } }));
        const original = { uid: "one", name: "Custom", columns: ["Queue"], is_default: true, is_builtin: false };
        const writes: { method: string; url: string; body: { name: string; description: string } }[] = [];
        let release!: () => void;
        const pending = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route("**/settings/project-templates**", async (route) => {
            if (route.request().method() === "GET") return route.fulfill({ json: { templates: [original] } });
            const body = route.request().postDataJSON();
            writes.push({ method: route.request().method(), url: route.request().url(), body });
            if (writes.length === 1) {
                await pending;
                if (scenario.startsWith("failed")) return route.fulfill({ status: 500, json: {} });
            }
            return route.fulfill({
                json: {
                    template: {
                        ...original,
                        ...body,
                        uid: scenario === "create" || scenario === "new session" ? "created" : "one",
                        columns: body.columns.map((column: { name: string }) => column.name),
                        column_definitions: body.columns,
                    },
                },
            });
        });
        await page.goto("/src/pages/SettingsPage/ProjectTemplates.fixture.html");
        await page
            .getByRole("button", { name: scenario === "create" || scenario === "new session" ? "New board template" : "Edit", exact: true })
            .click();
        await page.getByLabel("Board template name", { exact: true }).fill("Submitted");
        await page.getByLabel("Column name", { exact: true }).fill("Queue");
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes.length).toBe(1);
        if (scenario.includes("session")) {
            await page.getByRole("button", { name: scenario.includes("reopen") ? "Edit" : "New board template", exact: true }).click();
        }
        await page.getByLabel("Board template name", { exact: true }).fill("Later draft");
        await page.getByLabel("Column name", { exact: true }).fill("Later queue");
        await page.getByLabel("Board template description", { exact: true }).fill("Later description");
        release();
        await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
        await expect(page.getByLabel("Board template name", { exact: true })).toHaveValue("Later draft");
        await expect(page.getByLabel("Column name", { exact: true })).toHaveValue("Later queue");
        await expect(page.getByLabel("Board template description", { exact: true })).toHaveValue("Later description");
        await expect(page.getByRole("alert")).toHaveCount(0);
        if (!scenario.startsWith("failed")) {
            const selection = page.getByRole("combobox").first();
            await expect(selection).toContainText(scenario === "new session" ? "Custom" : "Submitted");
            await selection.click();
            await expect(page.getByRole("option", { name: "Submitted · Queue", exact: true })).toBeVisible();
            await page.keyboard.press("Escape");
        }
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await expect.poll(() => writes.length).toBe(2);
        await expect(page.locator("form")).toHaveCount(0);
        expect(writes[1].body).toMatchObject({ name: "Later draft", description: "Later description" });
        expect(writes[1].method).toBe(scenario.includes("new session") ? "POST" : "PUT");
        if (!scenario.includes("new session")) expect(writes[1].url).toContain(scenario === "create" ? "/created" : "/one");
    });
}
