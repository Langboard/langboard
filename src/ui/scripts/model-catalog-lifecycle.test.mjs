import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { test } from "node:test";

const source = process.env.LANGBOARD_MODEL_INPUT_SOURCE_BASE64
    ? Buffer.from(process.env.LANGBOARD_MODEL_INPUT_SOURCE_BASE64, "base64").toString()
    : readFileSync(new URL("../src/components/bots/BotValueInput/DefaultTypedInput.tsx", import.meta.url), "utf8");
const start = source.indexOf("function DefaultSelectInput(");
const end = source.indexOf("\n    const inputComp = ", start);
assert.ok(start >= 0 && end > start);
const body = stripTypeScriptTypes(source.slice(start, end) + "\nreturn { options, currentValue, fetchOptions, changeCurrentValue };\n}");
const deferred = () => {
    let resolve;
    const promise = new Promise((yes) => {
        resolve = yes;
    });
    return { promise, resolve };
};

// Bounded hook scheduler executes the production lifecycle body; no DOM, React renderer or provider transport is claimed.
function mount(input, initial = {}) {
    const slots = [],
        writes = [];
    let index = 0,
        dirty = true,
        mounted = true,
        output,
        frames = 0;
    const equal = (left, right) => left && right && left.length === right.length && left.every((value, key) => Object.is(value, right[key]));
    const effects = [];
    const useState = (initial) => {
        const key = index++;
        slots[key] ??= { value: typeof initial === "function" ? initial() : initial };
        return [
            slots[key].value,
            (value) => {
                if (!mounted) {
                    writes.push("after-unmount");
                    return;
                }
                const next = typeof value === "function" ? value(slots[key].value) : value;
                if (!Object.is(next, slots[key].value)) {
                    slots[key].value = next;
                    dirty = true;
                }
            },
        ];
    };
    const useCallback = (callback, deps) => {
        const key = index++;
        if (!equal(slots[key]?.deps, deps)) slots[key] = { value: callback, deps };
        return slots[key].value;
    };
    const useRef = (value) => {
        const key = index++;
        slots[key] ??= { current: value };
        return slots[key];
    };
    const useEffect = (callback, deps) => {
        const key = index++;
        if (!equal(slots[key]?.deps, deps)) {
            slots[key]?.cleanup?.();
            slots[key] = { deps };
            effects.push(() => {
                slots[key].cleanup = callback();
            });
        }
    };
    const context = {
        selectedProvider: "Ollama",
        valuesRef: { current: { ...initial } },
        setInputRef: () => {},
        setValue: (name) => (value) => {
            context.valuesRef.current[name] = value;
        },
        isValidating: false,
        required: true,
    };
    let collaborativeCallback;
    const collaborative = { remoteMeta: [], updateMeta: () => {}, updateValue: (value) => collaborativeCallback?.(value) };
    const useCollaborativeText = (options) => {
        collaborativeCallback = options.onValueChange;
        return collaborative;
    };
    const component = new Function(
        "useTranslation",
        "useBotValueDefaultInput",
        "useState",
        "useCallback",
        "useEffect",
        "useRef",
        "useCollaborativeText",
        "Utils",
        "API_URL",
        "api",
        `return ${body}`
    )(
        () => [(key) => key],
        () => context,
        useState,
        useCallback,
        useEffect,
        useRef,
        useCollaborativeText,
        {
            Type: {
                isObject: (value) => !!value && typeof value === "object",
                isString: (value) => typeof value === "string",
                isNumber: (value) => typeof value === "number",
            },
        },
        "https://example.invalid",
        {}
    );
    return {
        context,
        writes,
        get output() {
            return output;
        },
        get frames() {
            return frames;
        },
        async flush(limit = 12) {
            for (let frame = 0; frame < limit; frame++) {
                if (dirty && mounted) {
                    index = 0;
                    dirty = false;
                    frames++;
                    output = component({ input });
                    while (effects.length) effects.shift()();
                }
                await Promise.resolve();
                await Promise.resolve();
                if (!dirty) return true;
            }
            return false;
        },
        unmount() {
            for (const slot of slots) slot.cleanup?.();
            mounted = false;
        },
    };
}

test("an empty discovery settles after one load without mutating the descriptor", async () => {
    let calls = 0;
    const options = [];
    const input = {
        name: "model_name",
        options,
        getOptions: async () => {
            calls++;
            return [];
        },
    };
    const state = mount(input);
    const settled = await state.flush();
    state.unmount();
    assert.equal(settled, true, "empty lists must not schedule an unbounded render/load cycle");
    assert.equal(calls, 1);
    assert.equal(input.options, options);
});

test("selection changes do not reload; explicit refresh remains available", async () => {
    let calls = 0;
    const input = {
        name: "model_name",
        options: [],
        getOptions: async () => {
            calls++;
            return ["alpha", "beta"];
        },
    };
    const state = mount(input);
    assert.equal(await state.flush(), true);
    assert.equal(state.output.currentValue, "alpha");
    state.output.changeCurrentValue("beta");
    await state.flush();
    assert.equal(calls, 1);
    assert.equal(state.output.currentValue, "beta");
    await state.output.fetchOptions();
    await state.flush();
    assert.equal(calls, 2);
    assert.equal(state.output.currentValue, "beta");
    state.unmount();
});

test("a failed or empty later catalog preserves the selected model", async () => {
    const input = { name: "model_name", options: [], getOptions: async () => [] };
    const state = mount(input, { model_name: "retained" });
    assert.equal(await state.flush(), true);
    assert.equal(state.output.currentValue, "retained");
    assert.equal(state.context.valuesRef.current.model_name, "retained");
    state.unmount();
});

test("late discovery is discarded after the selector unmounts", async () => {
    const pending = deferred();
    const input = { name: "model_name", options: [], getOptions: () => pending.promise };
    const state = mount(input);
    await state.flush();
    state.unmount();
    pending.resolve(["late"]);
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(state.writes, []);
});

test("a superseded discovery cannot overwrite a newer manual refresh", async () => {
    const first = deferred(),
        second = deferred();
    let calls = 0;
    const input = { name: "model_name", options: [], getOptions: () => (++calls === 1 ? first.promise : second.promise) };
    const state = mount(input);
    await state.flush();
    const refreshed = state.output.fetchOptions();
    second.resolve(["new"]);
    await refreshed;
    await state.flush();
    first.resolve(["old"]);
    await Promise.resolve();
    await state.flush();
    assert.deepEqual(state.output.options, ["new"]);
    assert.equal(state.context.valuesRef.current.model_name, "new");
    state.unmount();
});
