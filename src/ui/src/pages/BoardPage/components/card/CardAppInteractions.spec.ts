import { test, expect } from "@playwright/test";
const fixture = "/src/pages/BoardPage/components/card/CardAppInteractions.fixture.html";
for (const width of [390, 1440]) {
    test(`lazy request, stable choice and receipt at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(fixture);
        await page.getByRole("button", { name: "App requests", exact: true }).waitFor();
        expect(await page.evaluate(() => (window as any).interactionCalls.length)).toBe(0);
        await page.getByRole("button", { name: "App requests", exact: true }).click();
        await page.getByRole("radio", { name: "Review", exact: true }).check();
        await page.getByRole("button", { name: "Submit response" }).click();
        await expect(page.getByText("Answered", { exact: true })).toBeVisible();
        await expect(page.getByText("Response recorded. This does not confirm external execution.")).toBeVisible();
        const writes = await page.evaluate(() => (window as any).interactionCalls.filter((call: any) => call.method === "post"));
        expect(writes).toHaveLength(1);
        expect(writes[0].data).toMatchObject({ expected_revision: 1, execution_generation: 7, values: { action: { choice_id: "review" } } });
        expect(writes[0].data.idempotency_key).toMatch(/^[A-Za-z0-9_-]+$/);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await page.screenshot({ path: `.verification/card-interactions-${width}.png` });
    });
}
test("unknown result blocks replay until readback", async ({ page }) => {
    await page.goto(`${fixture}?lost`);
    await page.getByRole("button", { name: "App requests", exact: true }).click();
    await page.getByRole("textbox", { name: "Text answer" }).fill("Additional review required");
    await page.getByRole("button", { name: "Submit response" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit response" })).toBeDisabled();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByText("Answered", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => (window as any).interactionCalls.filter((call: any) => call.method === "post").length)).toBe(1);
});
for (const flag of ["readonly", "expired", "reaction"]) {
    test(`${flag} cannot directly submit`, async ({ page }) => {
        await page.goto(`${fixture}?${flag}`);
        await page.getByRole("button", { name: "App requests", exact: true }).click();
        if (flag === "readonly") await expect(page.getByRole("button", { name: "Submit response" })).toBeDisabled();
        else await expect(page.getByRole("button", { name: "Submit response" })).toHaveCount(0);
        if (flag === "reaction") await expect(page.getByText(/Respond with a new reaction/)).toBeVisible();
        expect(await page.evaluate(() => (window as any).interactionCalls.filter((call: any) => call.method === "post").length)).toBe(0);
    });
}
test("multi choice enforces declared maximum", async ({ page }) => {
    await page.goto(`${fixture}?multi`);
    await page.getByRole("button", { name: "App requests", exact: true }).click();
    await page.getByRole("checkbox", { name: "Review" }).check();
    await expect(page.getByRole("button", { name: "Submit response" })).toBeEnabled();
    await page.getByRole("checkbox", { name: "Retry" }).check();
    await expect(page.getByRole("button", { name: "Submit response" })).toBeDisabled();
});

test("localized approval uses stable ID and execution fence", async ({ page }) => {
    await page.goto(`${fixture}?approval&locale=ko-KR`);
    await page.getByRole("button", { name: "앱 요청", exact: true }).click();
    await page.getByRole("radio", { name: "승인", exact: true }).check();
    await page.getByRole("button", { name: "응답 제출" }).click();
    await expect(page.getByText("응답 완료", { exact: true })).toBeVisible();
    const writes = await page.evaluate(() => (window as any).interactionCalls.filter((call: any) => call.method === "post"));
    expect(writes[0].data.values).toEqual({ action: { choice_id: "approve" } });
});
test("free text remains literal content", async ({ page }) => {
    await page.goto(`${fixture}?free`);
    await page.getByRole("button", { name: "App requests", exact: true }).click();
    await page.getByRole("textbox", { name: "Text answer" }).fill("<script>literal answer</script>");
    await page.getByRole("button", { name: "Submit response" }).click();
    await expect(page.getByText("Comment: <script>literal answer</script>", { exact: true })).toBeVisible();
});
for (const flag of ["move", "move&lost"]) {
    test(`explicit movement readback ${flag}`, async ({ page }) => {
        await page.goto(`${fixture}?${flag}`);
        await page.getByRole("button", { name: "App requests", exact: true }).click();
        await page.getByRole("button", { name: "Apply card move", exact: true }).click();
        if (flag.includes("lost")) {
            await expect(page.getByRole("alert")).toBeVisible();
            await expect(page.getByRole("button", { name: "Apply card move", exact: true })).toBeDisabled();
            await page.getByRole("button", { name: "Refresh", exact: true }).click();
        }
        await expect(page.getByText("Card movement recorded.", { exact: true })).toBeVisible();
        const writes = await page.evaluate(() => (window as any).interactionCalls.filter((call: any) => call.method === "post"));
        expect(writes).toHaveLength(1);
        expect(writes[0].data).toEqual({ response_uid: "00000000006", expected_card_revision: 12 });
    });
}
