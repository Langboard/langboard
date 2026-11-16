import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { stripTypeScriptTypes } from "node:module";

const source = process.env.LANGBOARD_PREFERENCE_SOURCE_BASE64
    ? Buffer.from(process.env.LANGBOARD_PREFERENCE_SOURCE_BASE64, "base64").toString()
    : readFileSync(new URL("../src/components/LanguageSwitcher/UserPreference.tsx", import.meta.url), "utf8");
const handler = source.match(/const handleUpdate = (\(lang: string\) => \{[\s\S]*?^    \});/m)?.[1];
assert.ok(handler, "Exercise the actual production handleUpdate, not a copied sequence");
const executable = stripTypeScriptTypes(handler);
const factory = new Function(
    "isValidating",
    "queryClient",
    "normalizeLocale",
    "i18n",
    "setIsValidating",
    "mutateAsync",
    "Toast",
    "t",
    "setupApiErrorHandler",
    "currentUser",
    `return ${executable}`
);
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => {
        resolve = yes;
        reject = no;
    });
    return { promise, resolve, reject };
};
function setup({ validating = false, writes = 0 } = {}) {
    const language = deferred(),
        save = deferred();
    const calls = [],
        states = [],
        currentUser = { preferred_lang: "en-US" };
    let operation, callbacks;
    const i18n = {
        language: "en-US",
        changeLanguage: (lang) => {
            calls.push(["language", lang]);
            if (lang === "en-US") {
                i18n.language = lang;
                return Promise.resolve();
            }
            return language.promise.then(() => {
                i18n.language = lang;
            });
        },
    };
    const run = factory(
        validating,
        { isMutating: () => writes },
        (lang) => lang,
        i18n,
        (state) => states.push(state),
        (input) => {
            calls.push(["save", input.lang]);
            return save.promise;
        },
        {
            Add: {
                promise: (promise, options) => {
                    operation = promise;
                    callbacks = options;
                },
            },
        },
        (key) => key,
        (_options, ref) => ({
            handle: () => {
                ref.message = "localized failure";
            },
        }),
        currentUser
    );
    return {
        run,
        language,
        save,
        calls,
        states,
        i18n,
        currentUser,
        get operation() {
            return operation;
        },
        get callbacks() {
            return callbacks;
        },
    };
}

test("profile persistence starts while a cold language pack is still pending", async () => {
    const state = setup();
    state.run("ja-JP");
    const saveStarted = state.calls.some(([kind]) => kind === "save");
    state.save.resolve();
    state.language.resolve();
    await state.operation;
    assert.equal(saveStarted, true, "a navigation during resource loading must not prevent the preference write from starting");
    state.callbacks.success();
    state.callbacks.finally();
    assert.equal(state.currentUser.preferred_lang, "ja-JP");
    assert.deepEqual(state.states, [true, false]);
});

test("optimistic locale can display while the one server write remains pending", async () => {
    const state = setup();
    state.run("ko-KR");
    state.language.resolve();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(state.i18n.language, "ko-KR");
    assert.equal(state.currentUser.preferred_lang, "en-US");
    state.save.resolve();
    await state.operation;
    state.callbacks.success();
    assert.equal(state.currentUser.preferred_lang, "ko-KR");
    assert.equal(state.calls.filter(([kind]) => kind === "save").length, 1);
});

test("failed preference save waits for the pending language before restoring it", async () => {
    const state = setup();
    state.run("zh-CN");
    const failure = new Error("synthetic server rejection");
    const settled = state.operation.then(
        () => ({ ok: true }),
        (error) => ({ error })
    );
    state.save.reject(failure);
    await Promise.resolve();
    await Promise.resolve();
    state.language.resolve();
    const result = await settled;
    assert.equal(result.error, failure);
    assert.equal(state.callbacks.error(result.error), "localized failure");
    state.callbacks.finally();
    assert.equal(state.i18n.language, "en-US");
    assert.equal(state.currentUser.preferred_lang, "en-US");
    assert.deepEqual(state.states, [true, false]);
});

test("either shared pending state prevents another preference request", () => {
    for (const options of [{ validating: true }, { writes: 1 }]) {
        const state = setup(options);
        state.run("ko-KR");
        assert.deepEqual(state.calls, []);
    }
});
