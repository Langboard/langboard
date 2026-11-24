import { expect, test } from "@playwright/test";
import { buildSync } from "esbuild";

// Bundle the real dependency graph, without loading unrelated application routes.
test.beforeAll(() => {
    buildSync({
        entryPoints: ["src/core/helpers/dnd/createDndSingleRootEvents.ts"],
        bundle: true,
        format: "esm",
        platform: "browser",
        tsconfig: "tsconfig.app.json",
        outfile: "test-results/single-dnd-root.mjs",
    });
});

test("single-list teardown releases scroll registration across remounts", async ({ page }) => {
    await page.goto("/src/controllers/api/board/refreshProjectColumnDock.fixture.html");
    const registrations = await page.evaluate(async () => {
        const modulePath = "/test-results/single-dnd-root.mjs";
        const { default: createRoot } = await import(modulePath);
        const element = document.createElement("div");
        element.style.overflow = "auto";
        document.body.append(element);
        const states: (string | null)[] = [];
        for (let mount = 0; mount < 3; mount++) {
            const cleanup = createRoot({
                rowsMap: {},
                symbolSet: { root: Symbol("root"), row: Symbol("row") },
                settings: { isOverElementAutoScrollEnabled: true, isOverflowScrollingEnabled: true, rootScrollSpeed: "standard" },
                scrollable: element,
                changeOrder: () => {},
            });
            states.push(element.getAttribute("data-auto-scrollable"));
            cleanup();
            states.push(element.getAttribute("data-auto-scrollable"));
        }
        element.remove();
        return states;
    });
    expect(registrations).toEqual(["true", null, "true", null, "true", null]);
});
