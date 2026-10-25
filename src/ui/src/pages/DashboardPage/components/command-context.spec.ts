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
        await expect(source).toHaveAttribute("aria-description", labels["Go to attachment source pages"]);
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

async function openPdfSource(page: import("@playwright/test").Page, mode = "valid") {
    await page.addInitScript(() => localStorage.setItem("lang", "en-US"));
    await page.goto(`/src/pages/DashboardPage/components/command-context.fixture.html?pdf=${mode}`);
    await page.getByRole("button", { name: "Open palette", exact: true }).click();
    await page.getByRole("combobox", { name: "Command palette", exact: true }).fill("document");
    await page.getByRole("option", { name: "Report.pdf · p. 2,1 · Found document", exact: true }).click();
}

test("authorized search source renders requested native Chrome PDF page and changes pages", async ({ page }, testInfo) => {
    const unrelated: string[] = [];
    page.on("request", (request) => {
        if (request.url().includes("untrusted.invalid")) unrelated.push(request.url());
    });
    await openPdfSource(page);
    await expect(page.locator("#attachment-paged-attachment")).toBeFocused();
    // Inspect the actual browser PDF extension, not merely the parent's #page fragment.
    const viewer = async () => {
        await expect.poll(() => page.frames().some((frame) => frame.url().startsWith("chrome-extension://"))).toBe(true);
        return page.frames().find((frame) => frame.url().startsWith("chrome-extension://"))!;
    };
    let native = await viewer();
    await expect(native.locator("#pageSelector")).toHaveValue("2");
    await expect
        .poll(() =>
            native.locator("pdf-viewer").evaluate((element) => {
                const viewer = element as unknown as {
                    viewport_: { getMostVisiblePage: () => number };
                    documentDimensions?: { pageDimensions: unknown[] };
                };
                return { page: viewer.viewport_.getMostVisiblePage(), pages: viewer.documentDimensions?.pageDimensions.length };
            })
        )
        .toEqual({ page: 1, pages: 2 });
    await page.screenshot({ path: testInfo.outputPath("native-page-two.png") });
    await page.getByRole("button", { name: "p. 1", exact: true }).click();
    native = await viewer();
    await expect(native.locator("#pageSelector")).toHaveValue("1");
    await page.screenshot({ path: testInfo.outputPath("native-page-one.png") });
    expect(unrelated).toEqual([]);
});

for (const mode of ["denied", "wrong-mime", "unsafe-url"]) {
    test(`PDF source rejects ${mode}`, async ({ page }) => {
        await openPdfSource(page, mode);
        await expect(page.locator("#attachment-paged-attachment")).toBeFocused();
        if (mode !== "unsafe-url") await expect(page.locator("html")).toHaveAttribute("data-pdf-request-state", "settled");
        await expect(page.locator("iframe")).toHaveCount(0);
        await expect(page.getByRole("link", { name: "Download PDF" })).toHaveCount(0);
    });
}

test("session change removes and revokes a displayed protected PDF", async ({ page }) => {
    await page.addInitScript(() => {
        const original = URL.revokeObjectURL.bind(URL);
        Object.assign(window, { revokedPdfUrls: [] as string[] });
        URL.revokeObjectURL = (url) => {
            (window as unknown as { revokedPdfUrls: string[] }).revokedPdfUrls.push(url);
            original(url);
        };
    });
    await openPdfSource(page);
    await expect(page.locator("iframe")).toHaveCount(1);
    const blob = (await page.locator("iframe").getAttribute("src"))!.split("#")[0];
    await page.evaluate(() =>
        document.querySelectorAll("button").forEach((button) => {
            if (button.textContent === "Change account session") button.click();
        })
    );
    await expect(page.locator("iframe")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate((url) => (window as unknown as { revokedPdfUrls: string[] }).revokedPdfUrls.includes(url), blob)).toBe(true);
});

test("session change rejects an outstanding PDF response", async ({ page }) => {
    await openPdfSource(page, "slow");
    // The click can resolve before React commits the source navigation. Revoke
    // the session only after a PDF request actually belongs to that session.
    await expect(page.locator("html")).toHaveAttribute("data-pdf-request-state", "pending");
    await page.evaluate(() =>
        document.querySelectorAll("button").forEach((button) => {
            if (button.textContent === "Change account session") button.click();
        })
    );
    await expect(page.locator("html")).toHaveAttribute("data-pdf-request-state", "settled");
    await expect(page.locator("iframe")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("invalid page and unrelated attachment routes cannot open PDF", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("lang", "en-US"));
    await page.goto("/src/pages/DashboardPage/components/command-context.fixture.html?pdf=valid");
    for (const query of [
        "attachment=missing&source-pages=2",
        "attachment=paged-attachment&source-pages=0",
        "attachment=paged-attachment&source-pages=2,-1",
        "attachment=paged-attachment&source-pages=1.5",
        "attachment=paged-attachment&source-pages=1000000000",
        "attachment=paged-attachment&source-pages=2&source-pages=1",
        "attachment=paged-attachment&attachment=other&source-pages=2",
    ]) {
        await page.getByRole("textbox", { name: "Source route" }).fill(`/board/fixture/document?${query}`);
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(page.locator("iframe")).toHaveCount(0);
    }
});

test("unsupported mobile PDF viewer offers an authenticated download and honest limitation", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => Object.defineProperty(navigator, "pdfViewerEnabled", { value: false }));
    await openPdfSource(page);
    await expect(
        page.getByText("This browser cannot display PDF pages inline. Download the PDF and open the requested page in a PDF viewer.")
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Download PDF" })).toHaveAttribute("href", /^blob:/);
    await expect(page.locator("iframe")).toHaveCount(0);
});
