import { expect, test, type Page } from "@playwright/test";

const fixture = "/src/pages/BoardPage/components/card/cardDraftClose.fixture.html";
const draftKey = (user = "a", card = "draft-close-card") => `lb-draft-v1:draft-close-user-${user}:draft-close-project:card-edit:${card}`;
async function stored(page: Page, user = "a", card = "draft-close-card") {
    return page.evaluate((key) => JSON.parse(localStorage.getItem(key) ?? "null")?.content ?? null, draftKey(user, card));
}
async function pauseDebounce(page: Page) {
    const now = Date.now();
    await page.clock.install({ time: now });
    await page.clock.pauseAt(now + 1000);
}
async function open(page: Page) {
    page.on("pageerror", (error) => console.log("Fixture page error:", error.message));
    await page.goto(fixture);
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Description draft")).toHaveValue("Original description");
}
async function reopen(page: Page, content: string) {
    await page.getByRole("button", { name: "Reopen card", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Description draft")).toHaveValue(content);
}

for (const persisted of [false, true]) {
    for (const dismiss of ["Close", "Escape", "backdrop"] as const) {
        test(`${dismiss} preserves ${persisted ? "persisted" : "pending debounce"} body without API save`, async ({ page }) => {
            await open(page);
            const content = `${dismiss} ${persisted ? "persisted" : "pending"} body`;
            if (!persisted) await pauseDebounce(page);
            await page.getByLabel("Description draft").fill(content);
            if (persisted) await expect.poll(() => stored(page)).toBe(content);
            else expect(await stored(page)).toBeNull();
            if (dismiss === "Close") await page.getByRole("button", { name: "Close", exact: true }).click();
            else if (dismiss === "Escape") await page.keyboard.press("Escape");
            else await page.mouse.click(2, 300);
            await expect.poll(() => stored(page)).toBe(content);
            await expect(page.getByText("Card closed", { exact: true })).toBeVisible();
            await expect(page.getByTestId("writes")).toHaveText("0");
            await reopen(page, content);
            if (!persisted) await page.clock.resume();
            await page.reload();
            await page.getByRole("button", { name: "Edit", exact: true }).click();
            await expect(page.getByLabel("Description draft")).toHaveValue(content);
        });
    }
}

test("explicit Cancel discards pending and persisted bodies", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Persisted discard");
    await expect.poll(() => stored(page)).toBe("Persisted discard");
    await pauseDebounce(page);
    await page.getByLabel("Description draft").fill("Pending discard");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(await stored(page)).toBeNull();
    await page.clock.runFor(700);
    expect(await stored(page)).toBeNull();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Description draft")).toHaveValue("Original description");
    await expect(page.getByTestId("writes")).toHaveText("0");
});

test("failed save then Close retains draft; successful retry clears it", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Failed request body");
    await page.getByRole("button", { name: "Toggle save failure", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect.poll(() => stored(page)).toBe("Failed request body");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Failed request body");
    await expect(page.getByText("Card closed", { exact: true })).toBeVisible();
    await reopen(page, "Failed request body");
    await page.getByRole("button", { name: "Toggle save failure", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    expect(await stored(page)).toBeNull();
});

test("route card switch flushes pending body into its own scope", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await page.getByLabel("Description draft").fill("Original card draft");
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Original card draft");
    await page.clock.resume();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Description draft")).toHaveValue("Original description");
    expect(await stored(page, "a", "draft-close-other-card")).toBeNull();
    await reopen(page, "Original card draft");
});

test("account switch after Close isolates retained draft and restores original account", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Account A private draft");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Account A private draft");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await reopen(page, "Original description");
    await page.getByLabel("Description draft").fill("Account B private draft");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => stored(page, "b")).toBe("Account B private draft");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await reopen(page, "Account A private draft");
    expect(await stored(page, "b")).toBe("Account B private draft");
});

test("same-user session rotation preserves closed draft without API save", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Session draft");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Session draft");
    await page.getByRole("button", { name: "Rotate session", exact: true }).click();
    await reopen(page, "Session draft");
    await expect(page.getByTestId("writes")).toHaveText("0");
});

test("empty pending body survives Close and reload", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await page.getByLabel("Description draft").fill("");
    expect(await stored(page)).toBeNull();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("");
    await expect(page.getByText("Card closed", { exact: true })).toBeVisible();
    await page.clock.resume();
    await page.reload();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Description draft")).toHaveValue("");
});

test("account switch flushes pending body only for its author", async ({ page }) => {
    await open(page);
    await pauseDebounce(page);
    await page.getByLabel("Description draft").fill("Pending account A body");
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("actor")).toHaveText("draft-close-user-b");
    await expect.poll(() => stored(page)).toBe("Pending account A body");
    await page.clock.resume();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Description draft")).toHaveValue("Original description");
    expect(await stored(page, "b")).toBeNull();
});

test("logout after Close hides author draft and same-author login restores it", async ({ page }) => {
    await open(page);
    await page.getByLabel("Description draft").fill("Private logout body");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => stored(page)).toBe("Private logout body");
    await page.getByRole("button", { name: "Log out", exact: true }).click();
    await expect(page.getByText("Signed out", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Description draft")).toHaveCount(0);
    await page.getByRole("button", { name: "Switch account", exact: true }).click();
    await expect(page.getByTestId("actor")).toHaveText("draft-close-user-a");
    await reopen(page, "Private logout body");
});
