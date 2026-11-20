import { expect, test, type Page } from "@playwright/test";
interface HydrationState {
    project?: string;
    card?: string;
    cardBoard?: string;
    metadata?: string;
    foreign: boolean;
    flipped: boolean;
    columns: number;
    checklists: number;
    relations: number;
    projectStatus: string;
    cardsStatus: string;
}
declare global {
    interface Window {
        hydration: {
            snapshot: () => HydrationState;
            requests: string[];
            metadataScopes: unknown[];
            completed: { kind: string; aborted: boolean }[];
            release: () => void;
            omit: () => void;
            retain: () => void;
            switchActor: () => void;
            logout: () => void;
            switchBoard: () => void;
            refresh: () => void;
        };
    }
}
const fixture = "/src/controllers/api/board/board-hydration.fixture.html";
const snapshot = (page: Page) => page.evaluate(() => window.hydration.snapshot());
const action = (page: Page, name: "release" | "retain" | "switchActor" | "logout" | "switchBoard" | "refresh" | "omit") =>
    page.evaluate((name) => window.hydration[name](), name);
const requests = (page: Page) => page.evaluate(() => window.hydration.requests as string[]);
for (const kind of ["project", "cards", "metadata"]) {
    for (const transition of ["switchActor", "logout"] as const) {
        test(`retained ${kind} response after ${transition} cannot hydrate`, async ({ page }) => {
            await page.goto(`${fixture}?delay=${kind}`);
            await expect
                .poll(async () =>
                    (await requests(page)).some((url) =>
                        kind === "project" ? url === "/board/board" : kind === "cards" ? url.endsWith("/cards") : url.includes("/metadata/")
                    )
                )
                .toBe(true);
            await action(page, "retain");
            await action(page, transition);
            if (transition === "switchActor") await expect.poll(async () => (await snapshot(page)).card).toBe("actor-b");
            await action(page, "release");
            await page.waitForTimeout(100);
            const state = await snapshot(page);
            expect(state.project).toBe(transition === "logout" ? undefined : "actor-b");
            expect(state.card).toBe(transition === "logout" ? undefined : "actor-b");
            expect(state.metadata).not.toBe("actor-a");
            if (transition === "switchActor") {
                const completed = await page.evaluate(() => window.hydration.completed);
                expect(
                    completed
                        .filter((entry: { kind: string; aborted: boolean }) => entry.kind === kind)
                        .every((entry: { aborted: boolean }) => !entry.aborted)
                ).toBe(true);
            }
        });
    }
}
test("fast cached actor switch changes cache owner", async ({ page }) => {
    await page.goto(fixture);
    await expect.poll(async () => (await snapshot(page)).metadata).toBe("actor-a");
    await action(page, "switchActor");
    await expect.poll(async () => (await snapshot(page)).metadata).toBe("actor-b");
});
for (const wrong of ["project", "card", "column", "checklist", "label", "relationship", "checkitem"]) {
    test(`wrong ${wrong} payload rejected before any snapshot mutation`, async ({ page }) => {
        await page.goto(`${fixture}?wrong=${wrong}`);
        await expect.poll(async () => (await snapshot(page))[wrong === "project" ? "projectStatus" : "cardsStatus"]).toBe("error");
        const state = await snapshot(page);
        if (wrong === "project") expect(state.foreign).toBe(false);
        else {
            expect(state.card).toBeUndefined();
            expect(state.columns).toBe(0);
            expect(state.checklists).toBe(0);
            expect(state.relations).toBe(0);
        }
    });
}
test("normal refresh and idle preserve optional lazy behavior", async ({ page }) => {
    await page.goto(fixture);
    await expect.poll(async () => (await snapshot(page)).metadata).toBe("actor-a");
    const first = await requests(page);
    expect(await page.evaluate(() => window.hydration.metadataScopes)).toEqual([true]);
    expect(first.some((url) => url.includes("automation"))).toBe(false);
    await page.waitForTimeout(150);
    expect(await requests(page)).toEqual(first);
    await action(page, "refresh");
    await expect.poll(async () => (await requests(page)).length).toBeGreaterThan(first.length);
    expect((await snapshot(page)).card).toBe("actor-a");
});
test("disabled cards observer starts no card or metadata request", async ({ page }) => {
    await page.goto(fixture + "?disabled=1");
    await expect.poll(async () => (await snapshot(page)).project).toBe("actor-a");
    expect((await requests(page)).some((url) => url.includes("/cards") || url.includes("/metadata/"))).toBe(false);
});

for (const kind of ["project", "cards", "metadata"]) {
    test(`retained ${kind} response after board change cannot hydrate prior board`, async ({ page }) => {
        await page.goto(`${fixture}?delay=${kind}`);
        await expect
            .poll(async () =>
                (await requests(page)).some((url) =>
                    kind === "project" ? url === "/board/board" : kind === "cards" ? url.endsWith("/cards") : url.includes("/metadata/")
                )
            )
            .toBe(true);
        await action(page, "retain");
        await action(page, "switchBoard");
        await expect.poll(async () => (await snapshot(page)).cardBoard).toBe("next-board");
        await action(page, "release");
        await page.waitForTimeout(100);
        expect((await snapshot(page)).cardBoard).toBe("next-board");
        if (kind === "project") expect((await snapshot(page)).project).toBeUndefined();
    });
}

test("same actor archive omission preserves flipped card availability", async ({ page }) => {
    await page.goto(fixture);
    await expect.poll(async () => (await snapshot(page)).card).toBe("actor-a");
    await action(page, "omit");
    await expect.poll(async () => (await snapshot(page)).card).toBeUndefined();
    expect((await snapshot(page)).flipped).toBe(true);
});
