import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const overrides = process.env.LANGBOARD_CRON_PLACEHOLDERS_BASE64
    ? JSON.parse(Buffer.from(process.env.LANGBOARD_CRON_PLACEHOLDERS_BASE64, "base64").toString())
    : null;
const locales = ["ko-KR", "en-US", "ja-JP", "zh-CN"];
const resources = Object.fromEntries(
    locales.map((locale) => [
        locale,
        overrides?.resources[locale] ?? JSON.parse(readFileSync(new URL(`../src/assets/locales/${locale}/cron.json`, import.meta.url), "utf8")),
    ])
);
const fields = {
    MonthDays: ["cron.every day of the month", "cron.day of the month"],
    WeekDays: ["cron.every day of the week", "cron.day of the week"],
};
const translator = (locale) => (key) => resources[locale][key.slice("cron.".length)];

function mount(name) {
    const source = overrides?.sources[name] ?? readFileSync(new URL(`../src/components/Cron/fields/${name}.tsx`, import.meta.url), "utf8");
    const start = source.indexOf("const placeholder = useMemo(");
    const end = source.indexOf("]);", start) + 3;
    assert.ok(start >= 0 && end > start, `${name}: production memo not found`);
    // Execute the production memo and dependencies. This is not a React renderer or browser test.
    const render = new Function("useMemo", "t", "noWeekDays", "noMonthDays", `${source.slice(start, end)}\nreturn placeholder;`);
    let previous, value;
    const useMemo = (calculate, dependencies) => {
        if (!previous || dependencies.some((dependency, index) => !Object.is(dependency, previous[index]))) {
            value = calculate();
            previous = dependencies;
        }
        return value;
    };
    return (t, empty) => render(useMemo, t, empty, empty);
}

for (const [name, keys] of Object.entries(fields)) {
    test(`${name} updates both placeholder branches when only the locale changes`, () => {
        for (const empty of [true, false]) {
            const render = mount(name);
            for (const locale of locales) {
                const key = keys[empty ? 0 : 1];
                const expected = translator(locale)(key);
                assert.equal(typeof expected, "string", `${locale}: ${key}`);
                assert.equal(render(translator(locale), empty), expected, `${name}/${locale}/${key}`);
            }
        }
    });
}

test("day-selection changes still recompute each field within the same locale", () => {
    const t = translator("en-US");
    for (const [name, keys] of Object.entries(fields)) {
        const render = mount(name);
        assert.equal(render(t, true), t(keys[0]));
        assert.equal(render(t, false), t(keys[1]));
        assert.equal(render(t, true), t(keys[0]));
    }
});
