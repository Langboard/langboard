import { expect, test } from "@playwright/test";

for (const embedded of [false, true]) {
    test(`existing board creation ${embedded ? "returns a source board to its caller" : "keeps default board navigation"}`, async ({ page }) => {
        await page.route("**/settings/project-templates", (route) =>
            route.fulfill({ json: { templates: [{ uid: "legacy", name: "Legacy", columns: ["Queue"], is_default: true }] } })
        );
        const requests: object[] = [];
        await page.route("**/dashboard/projects/new", (route) => {
            requests.push(route.request().postDataJSON());
            return route.fulfill({ json: { project_uid: "new-source-board" } });
        });
        await page.goto(`/src/pages/DashboardPage/components/TemplateSelection.fixture.html${embedded ? "?embedded" : ""}`);
        await page.getByRole("combobox").last().click();
        await page.getByRole("option", { name: "Legacy · Queue", exact: true }).click();
        await page.locator("input[name=title]").fill("Source board");
        await page.getByRole("combobox").first().fill("Other");
        await page.getByRole("button", { name: "Create", exact: true }).click();
        await expect(page.locator("output")).toHaveText(
            JSON.stringify({ opened: !embedded, created: embedded ? ["new-source-board"] : [], path: embedded ? "/" : "/board/new-source-board" })
        );
        if (embedded) await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(requests).toEqual([{ title: "Source board", description: "", project_type: "Other", template_name: "Legacy" }]);
    });
}

for (const hasTemplate of [true, false])
    test(`failed embedded creation keeps draft without retry (template=${hasTemplate})`, async ({ page }) => {
        await page.route("**/settings/project-templates", (route) =>
            route.fulfill({
                json: { templates: hasTemplate ? [{ uid: "legacy", name: "Legacy", columns: ["Queue"], is_builtin: false, is_default: true }] : [] },
            })
        );
        let requests = 0;
        await page.route("**/dashboard/projects/new", (route) => {
            requests++;
            return route.fulfill({ status: 500, json: { message: "creation failed" } });
        });
        await page.goto("/src/pages/DashboardPage/components/TemplateSelection.fixture.html?embedded");
        if (hasTemplate) {
            await page.getByRole("combobox").last().click();
            await page.getByRole("option", { name: "Legacy · Queue", exact: true }).click();
        }
        await page.locator("input[name=title]").fill("Preserved source draft");
        await page.getByRole("combobox").first().fill("Other");
        await page.getByRole("button", { name: "Create", exact: true }).click();
        await expect.poll(() => requests).toBe(1);
        await expect(page.getByRole("button", { name: "Create", exact: true })).toBeEnabled();
        await expect(page.locator("output")).toHaveText(JSON.stringify({ opened: true, created: [], path: "/" }));
        await expect(page.locator("input[name=title]")).toHaveValue("Preserved source draft");
    });

for (const width of [1280, 390])
    for (const [locale, expected] of [
        ["en-US", "Backlog → Review"],
        ["ko-KR", "요청 → 검토"],
        ["ja-JP", "依頼 → 確認"],
        ["zh-CN", "请求 → Review"],
    ]) {
        test(`template selection consumes locale columns and retains canonical key at ${locale}/${width}`, async ({ page }) => {
            await page.setViewportSize({ width, height: 850 });
            await page.route("**/settings/project-templates", (route) =>
                route.fulfill({
                    json: {
                        templates: [
                            {
                                uid: "builtin",
                                name: "SI",
                                is_builtin: true,
                                is_default: true,
                                columns: ["Backlog", "Review"],
                                column_definitions: [
                                    {
                                        name: "Backlog",
                                        description: "Waiting",
                                        workflow_stage: "backlog",
                                        translations: {
                                            ko: { name: "요청", description: "" },
                                            ja: { name: "依頼", description: "" },
                                            zh: { name: "请求", description: "" },
                                        },
                                    },
                                    {
                                        name: "Review",
                                        description: "Acceptance",
                                        workflow_stage: "review",
                                        translations: {
                                            ko: { name: "검토", description: "" },
                                            ja: { name: "確認", description: "" },
                                            zh: { name: "", description: "" },
                                        },
                                    },
                                ],
                            },
                            { uid: "legacy", name: "Legacy", columns: ["Queue"], is_builtin: false, is_default: false },
                        ],
                    },
                })
            );
            await page.goto(`/src/pages/DashboardPage/components/TemplateSelection.fixture.html?lang=${locale}`);
            const selected = page.getByRole("combobox").last();
            await selected.click();
            await expect(page.getByRole("option", { name: `SI · ${expected}`, exact: true })).toBeVisible();
            await page.getByRole("option", { name: `SI · ${expected}`, exact: true }).click();
            await expect(page.locator("input[name=template_name]")).toHaveValue("SI");
            await selected.click();
            await page.getByRole("option", { name: "Legacy · Queue", exact: true }).click();
            await expect(page.locator("input[name=template_name]")).toHaveValue("Legacy");
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        });
    }
