import { test, expect } from "@playwright/test";

test("global label save rejects duplicate submissions and permits explicit retry", async ({ page }) => {
    let writes = 0;
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/global-labels**", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
        if (route.request().method() === "GET") return route.fulfill({ json: { labels: [] } });
        writes++;
        await pending;
        return route.fulfill({ status: 500, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html");
    await page.getByLabel("Label name", { exact: true }).fill("Question");
    await page.locator("form").evaluate((form) => {
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    try {
        await expect.poll(() => writes).toBe(1);
        await expect(page.locator("form button[type=submit]")).toBeDisabled();
        await page.waitForTimeout(100);
        expect(writes).toBe(1);
    } finally {
        release();
    }
    await expect(page.locator("form").getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Label name", { exact: true })).toHaveValue("Question");
    await page.locator("form").getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => writes).toBe(2);
});

for (const width of [1280, 390])
    test(`global label translations persist at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        let labels: object[] = [];
        await page.route("**/settings/global-labels**", async (route) => {
            const headers = {
                "Access-Control-Allow-Origin": "http://127.0.0.1:4193",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
                "Access-Control-Allow-Headers": "content-type,authorization,content-encoding",
            };
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            if (route.request().method() === "GET") return route.fulfill({ headers, json: { labels } });
            const label = { ...route.request().postDataJSON(), uid: "saved" };
            labels = [label];
            return route.fulfill({ headers, json: { label } });
        });
        await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html");
        await expect(page.getByText("No global labels yet")).toBeVisible();
        await page.getByLabel("Label name", { exact: true }).fill("Request");
        await page.getByLabel("Label description", { exact: true }).fill("English guidance");
        await page.getByLabel("Emoji (optional)", { exact: true }).fill("🐛");
        await page.getByLabel("Title aliases", { exact: true }).fill("Defect\n障害");
        await page.getByRole("button", { name: "Color", exact: true }).click();
        const picker = page.locator("[data-radix-popper-content-wrapper]").last();
        await expect(picker.locator(".react-colorful")).toBeVisible();
        await picker.locator("input").fill("#FF9500");
        await picker.getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByLabel("Color hex code", { exact: true })).toHaveValue("#FF9500");
        await expect(page.getByRole("navigation").getByRole("button")).toHaveCount(0);
        await page.getByLabel("Color hex code", { exact: true }).fill("#8B5CF6");
        await page.getByRole("button", { name: "한국어", exact: true }).click();
        await page.getByLabel("Label name", { exact: true }).fill("요청");
        await page.getByLabel("Label description", { exact: true }).fill("한국어 설명");
        await page.getByRole("button", { name: "English", exact: true }).click();
        await expect(page.getByLabel("Label name", { exact: true })).toHaveValue("Request");
        await page.getByLabel("Language code", { exact: true }).fill("fr");
        await page.getByRole("button", { name: "Add language", exact: true }).click();
        await page.getByLabel("Label name", { exact: true }).fill("Demande");
        await page.locator("form").getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("navigation").getByRole("button", { name: "🐛 Request", exact: true })).toBeVisible();
        await page.reload();
        await page.getByRole("navigation").getByRole("button", { name: "🐛 Request", exact: true }).click();
        await expect(page.getByLabel("Emoji (optional)", { exact: true })).toHaveValue("🐛");
        await expect(page.getByLabel("Title aliases", { exact: true })).toHaveValue("Defect\n障害");
        await expect(page.getByLabel("Color hex code", { exact: true })).toHaveValue("#8B5CF6");
        await expect(page.getByLabel("Label name", { exact: true })).toHaveValue("Request");
        await page.getByRole("button", { name: "한국어", exact: true }).click();
        await expect(page.getByLabel("Label name", { exact: true })).toHaveValue("요청");
        await expect(page.getByLabel("Label description", { exact: true })).toHaveValue("한국어 설명");
        await page.getByRole("button", { name: "fr", exact: true }).click();
        await expect(page.getByLabel("Label name", { exact: true })).toHaveValue("Demande");
        await page.getByLabel("Emoji (optional)", { exact: true }).fill("");
        await page.locator("form").getByRole("button", { name: "Save", exact: true }).click();
        await expect(page.getByRole("navigation").getByRole("button", { name: "Request", exact: true })).toBeVisible();
        await page.reload();
        await page.getByRole("navigation").getByRole("button", { name: "Request", exact: true }).click();
        await expect(page.getByLabel("Emoji (optional)", { exact: true })).toHaveValue("");
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });

test("global label list prefers exact locale then language while preserving canonical edit fields", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("lang", "ko-KR"));
    await page.route("**/settings/global-labels**", (route) =>
        route.fulfill({
            json: {
                labels: [
                    {
                        uid: "fixture",
                        name: "Contract",
                        description: "English contract",
                        color: "#8B5CF6",
                        emoji: "📜",
                        translations: { "ko-KR": { name: "지역 계약", description: "" }, ko: { name: "계약", description: "언어 설명" } },
                    },
                ],
            },
        })
    );
    await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html");
    await page.getByRole("navigation").getByRole("button", { name: "📜 지역 계약", exact: true }).click();
    await expect(page.locator("input[value=Contract]")).toBeVisible();
    await expect(page.getByLabel("레이블 설명", { exact: true })).toHaveValue("English contract");
});

for (const width of [1920, 390])
    test(`failed global label list is recoverable without writes at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        let reads = 0;
        let writes = 0;
        await page.route("**/settings/global-labels**", (route) => {
            if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
            if (route.request().method() !== "GET") {
                writes++;
                return route.fulfill({ status: 500, json: {} });
            }
            reads++;
            return reads === 1
                ? route.fulfill({ status: 503, json: {} })
                : route.fulfill({
                      json: { labels: [{ uid: "existing", name: "Question", description: "Ask", color: "#8B5CF6", translations: {} }] },
                  });
        });
        await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html");
        await expect(page.getByRole("alert")).toBeVisible();
        await expect(page.getByText("No global labels yet", { exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "Retry", exact: true }).click();
        await expect(page.getByRole("navigation").getByRole("button", { name: "Question", exact: true })).toBeVisible();
        await expect(page.getByRole("alert")).toHaveCount(0);
        expect(reads).toBe(2);
        expect(writes).toBe(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

test("read-only global label editor rejects form submission", async ({ page }) => {
    let writes = 0;
    await page.route("**/settings/global-labels**", (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
        if (route.request().method() !== "GET") {
            writes++;
            return route.fulfill({ status: 403, json: {} });
        }
        return route.fulfill({
            json: { labels: [{ uid: "one", name: "Contract", description: "Interface agreement", color: "#8B5CF6", translations: {} }] },
        });
    });
    await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html?readonly");
    await page.getByRole("navigation").getByRole("button", { name: "Contract", exact: true }).click();
    await expect(page.getByLabel("Label name", { exact: true })).toBeDisabled();
    await expect(page.getByLabel("Label description", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "New label", exact: true })).toHaveCount(0);
    await expect(page.locator("form").getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    // Native submit events can originate independently of a visible Save button.
    await page.locator("form").evaluate((form: HTMLFormElement) => form.requestSubmit());
    await page.waitForTimeout(100);
    expect(writes).toBe(0);
});

for (const target of ["current new", "current existing", "other", "new"])
    test(`delayed global label save preserves ${target} draft session`, async ({ page }) => {
        const existing = { uid: "existing", name: "Existing", description: "", color: "#8B5CF6", translations: {} };
        const writes: { method: string; url: string; body: Record<string, unknown> }[] = [];
        let release!: () => void;
        const pending = new Promise<void>((resolve) => {
            release = resolve;
        });
        await page.route("**/settings/global-labels**", async (route) => {
            const method = route.request().method();
            if (method === "OPTIONS") return route.fulfill({ status: 204 });
            if (method === "GET") return route.fulfill({ json: { labels: [existing] } });
            const body = route.request().postDataJSON();
            writes.push({ method, url: route.request().url(), body });
            if (writes.length === 1) await pending;
            return route.fulfill({ json: { label: { ...body, uid: method === "PUT" ? "existing" : "created" } } });
        });
        await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html");
        const name = page.getByLabel("Label name", { exact: true });
        const save = page.locator("form button[type=submit]");
        if (target === "current existing") await page.getByRole("navigation").getByRole("button", { name: "Existing", exact: true }).click();
        await name.fill("Submitted");
        await save.click();
        await expect.poll(() => writes.length).toBe(1);
        page.on("dialog", (dialog) => dialog.accept());
        if (target === "other") await page.getByRole("navigation").getByRole("button", { name: "Existing", exact: true }).click();
        if (target === "new") await page.getByRole("button", { name: "New label", exact: true }).click();
        await name.fill("Later edit");
        await page.getByLabel("Title aliases", { exact: true }).fill("Later alias");
        await page.getByRole("button", { name: "한국어", exact: true }).click();
        await name.fill("나중 편집");
        release();
        await expect(save).toBeEnabled();
        await expect(name).toHaveValue("나중 편집");
        await expect(page.getByLabel("Title aliases", { exact: true })).toHaveValue("Later alias");
        await page.getByRole("button", { name: "English", exact: true }).click();
        await expect(name).toHaveValue("Later edit");
        const selectedName = target === "other" || target === "current existing" ? "Existing" : "Submitted";
        await expect(page.getByRole("navigation").getByRole("button", { name: selectedName, exact: true })).toHaveAttribute(
            "aria-pressed",
            target === "new" ? "false" : "true"
        );
        await save.click();
        await expect.poll(() => writes.length).toBe(2);
        expect(writes[1].method).toBe(target === "new" ? "POST" : "PUT");
        if (target !== "new") expect(writes[1].url).toContain(target === "other" || target === "current existing" ? "/existing" : "/created");
        expect(writes[1].body.name).toBe("Later edit");
        expect(writes[1].body.aliases).toEqual(["Later alias"]);
        expect(writes[1].body.translations).toMatchObject({ ko: { name: "나중 편집" } });
    });

test("failed delayed global label save preserves later edits for retry", async ({ page }) => {
    let writes = 0;
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/settings/global-labels**", async (route) => {
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
        if (route.request().method() === "GET") return route.fulfill({ json: { labels: [] } });
        writes++;
        await pending;
        return route.fulfill({ status: 500, json: {} });
    });
    await page.goto("/src/pages/SettingsPage/GlobalLabels.fixture.html");
    const name = page.getByLabel("Label name", { exact: true });
    const save = page.locator("form button[type=submit]");
    await name.fill("Submitted");
    await save.click();
    await expect.poll(() => writes).toBe(1);
    await name.fill("Later edit");
    release();
    await expect(save).toBeEnabled();
    await expect(name).toHaveValue("Later edit");
    await save.click();
    await expect.poll(() => writes).toBe(2);
});
