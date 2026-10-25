import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
for (const viewport of [
    { width: 1280, height: 720 },
    { width: 390, height: 844 },
]) {
    test(`commands retain board route and focus visible pending panel at ${viewport.width}px`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
        for (const [label, mode] of [
            ["Changes", "changes"],
            ["Relations", "relations"],
            ["My Work", "my-work"],
            ["Changes", "changes"],
        ]) {
            await page.getByRole("button", { name: "Open palette", exact: true }).press("Enter");
            const input = page.getByRole("combobox", { name: "Command palette", exact: true });
            await input.fill(label);
            await input.press("End");
            await input.press("Enter");
            const target = page.getByRole(viewport.width < 768 ? "complementary" : "region", { name: mode, exact: true });
            await expect(target).toBeFocused();
            if (viewport.width < 768) {
                await target.press("Tab");
                await expect(page.getByRole("button", { name: "Close panel", exact: true })).toBeFocused();
            }
            await expect(page.getByTestId("route")).toHaveText("/board/fixture");
            await expect(page.getByText("Panel content pending", { exact: true })).toBeVisible();
        }
        await page.getByRole("button", { name: "Open palette", exact: true }).press("Enter");
        await page.getByRole("combobox", { name: "Command palette", exact: true }).press("Escape");
        await expect(page.getByRole("button", { name: "Open palette", exact: true })).toBeFocused();
    });
}

for (const language of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
    const labels = JSON.parse(readFileSync(new URL(`../../../assets/locales/${language}/dashboard.json`, import.meta.url), "utf8"));
    test(`visible command labels are searchable in ${language}`, async ({ page }) => {
        await page.addInitScript((lang) => localStorage.setItem("lang", lang), language);
        await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
        await page.getByRole("button", { name: "Open palette", exact: true }).click();
        const input = page.getByRole("combobox", { name: labels["Command palette"], exact: true });
        for (const key of ["New card", "New project", "Toggle sidebar", "My Work", "Changes", "Relations"]) {
            const label = labels[key];
            await input.fill(label);
            await expect(page.getByRole("option", { name: label, exact: true })).toBeVisible();
        }
        await input.fill(labels.Changes);
        await input.press("Enter");
        await expect(page.getByRole("region", { name: "changes", exact: true })).toBeFocused();
        await expect(page.getByTestId("route")).toHaveText("/board/fixture");
        await page.getByRole("button", { name: "Open palette", exact: true }).click();
        await page.getByRole("combobox", { name: labels["Command palette"], exact: true }).fill("document");
        const pageLabel = labels["Source pages"].replace("{{pages}}", "3,5");
        const source = page.getByRole("option", { name: `Report.pdf · ${pageLabel} · Found document`, exact: true });
        await expect(source).toHaveAttribute("aria-description", labels["Go to attachment; page navigation unavailable"]);
        await source.click();
        await expect(page.locator("#attachment-paged-attachment")).toBeFocused();
        await expect(page.getByRole("note")).toHaveText(labels["Search attachment source limitation"].replace("{{pages}}", "3, 5"));
    });
}

test("board transcript search displays sources, hides stale results and opens the owning card", async ({ page }) => {
    await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
    await page.getByRole("button", { name: "Open palette", exact: true }).click();
    const input = page.getByRole("combobox", { name: "Command palette", exact: true });
    await input.fill("전사검색");
    await expect(page.getByRole("option", { name: /^Found 전사검색/ })).toBeVisible();
    await expect(page.getByText("Meeting.pdf · 전사검색 日本語 中文 document", { exact: true })).toBeVisible();
    await input.fill("slow");
    await expect(page.getByRole("option", { name: /^Found 전사검색/ })).toBeHidden();
    await page.waitForTimeout(350);
    await input.fill("latest");
    const latest = page.getByRole("option", { name: /^Found latest/ });
    await expect(latest).toBeVisible();
    await page.waitForTimeout(1100);
    await expect(page.getByRole("option", { name: /^Found slow/ })).toBeHidden();
    await latest.click();
    await expect(page.getByTestId("route")).toHaveText("/board/fixture/latest");
});

for (const width of [1280, 390]) {
    for (const keyboard of [false, true]) {
        test(`document source ${keyboard ? "keyboard" : "click"} opens exact receiver at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 844 });
            await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
            await page.getByRole("button", { name: "Open palette", exact: true }).click();
            const input = page.getByRole("combobox", { name: "Command palette", exact: true });
            await input.fill("document");
            const source = page.getByRole("option", { name: "Report.pdf · p. 3,5 · Found document", exact: true });
            await expect(source).toBeVisible();
            if (keyboard) {
                for (let index = 0; index < 15; index++) {
                    if ((await source.getAttribute("data-selected")) === "true") break;
                    await input.press("ArrowDown");
                }
                await expect(source).toHaveAttribute("data-selected", "true");
                await input.press("Enter");
            } else await source.click();
            await expect(page.getByTestId("route")).toHaveText(
                "/board/fixture/document?attachment=paged-attachment&source-pages=3%2C5#attachment-paged-attachment"
            );
            const receiver = page.locator("#attachment-paged-attachment");
            await expect(receiver).toBeFocused();
            await expect(receiver.getByRole("note")).toHaveText("Search source: p. 3, 5. Attachment located; page navigation unavailable.");
            await expect(page.getByRole("dialog")).toBeHidden();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        });
    }
}

test("document source without page opens attachment anchor", async ({ page }) => {
    await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
    await page.getByRole("button", { name: "Open palette", exact: true }).click();
    await page.getByRole("combobox", { name: "Command palette", exact: true }).fill("document");
    await page.getByRole("option", { name: "Meeting.pdf · Found document", exact: true }).click();
    await expect(page.getByTestId("route")).toHaveText("/board/fixture/document?attachment=attachment#attachment-attachment");
    await expect(page.locator("#attachment-attachment")).toBeFocused();
    await expect(page.getByRole("note")).toHaveCount(0);
});

test("account change rejects outstanding document search results", async ({ page }) => {
    await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
    await page.getByRole("button", { name: "Open palette", exact: true }).click();
    const input = page.getByRole("combobox", { name: "Command palette", exact: true });
    await input.fill("slow");
    await page.waitForTimeout(400);
    await page.evaluate(() =>
        document.querySelectorAll("button").forEach((button) => {
            if (button.textContent === "Change account session") button.click();
        })
    );
    await expect(page.getByRole("option", { name: /^Found slow new session/ })).toBeVisible();
    await expect(page.getByRole("option", { name: "Found slow Backlog", exact: true })).toBeHidden();
    await expect(page.getByRole("option", { name: "Meeting.pdf · Found slow", exact: true })).toBeHidden();
});

test("account change hides already visible document sources before new session resolves", async ({ page }) => {
    await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html");
    await page.getByRole("button", { name: "Open palette", exact: true }).click();
    await page.getByRole("combobox", { name: "Command palette", exact: true }).fill("slow");
    const previousSource = page.getByRole("option", { name: "Meeting.pdf · Found slow", exact: true });
    await expect(previousSource).toBeVisible();
    await page.evaluate(() =>
        document.querySelectorAll("button").forEach((button) => {
            if (button.textContent === "Change account session") button.click();
        })
    );
    await expect(previousSource).toBeHidden();
    await expect(page.getByRole("option", { name: "Meeting.pdf · Found slow new session", exact: true })).toBeVisible();
});
