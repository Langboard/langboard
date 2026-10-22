import assert from "node:assert/strict";
import { test } from "node:test";
import { getOllamaModelStore, TBaseOllamaModel, updateProgressCallback } from "./OllamaModelStore";

test("model and pull mutations preserve prior store snapshots", () => {
    const model: TBaseOllamaModel = {
        name: "store-test-model",
        size: 1,
        digest: "digest",
        modified_at: new Date(0),
        details: {
            format: "gguf",
            family: "test",
            families: null,
            parameter_size: "1B",
            quantization_level: "Q4",
        },
    };
    const previousModels = getOllamaModelStore().models;
    const previousPulls = getOllamaModelStore().pullingModels;

    try {
        getOllamaModelStore().upsertModel(model);
        const withModel = getOllamaModelStore().models;
        assert.notStrictEqual(withModel, previousModels);
        assert.equal(previousModels[model.name], undefined);
        assert.deepEqual(withModel[model.name], model);

        getOllamaModelStore().upsertPullingModel({ name: model.name, isTracking: true, progress: 20 });
        const withPull = getOllamaModelStore().pullingModels;
        assert.notStrictEqual(withPull, previousPulls);
        assert.equal(previousPulls[model.name], undefined);
        assert.equal(withPull[model.name]?.progress, 20);

        getOllamaModelStore().deletePullingModel(model.name);
        assert.equal(getOllamaModelStore().pullingModels[model.name], undefined);
        assert.equal(withPull[model.name]?.progress, 20);

        getOllamaModelStore().deleteModel(model.name);
        assert.equal(getOllamaModelStore().models[model.name], undefined);
        assert.deepEqual(withModel[model.name], model);
    } finally {
        getOllamaModelStore().deletePullingModel(model.name);
        getOllamaModelStore().deleteModel(model.name);
    }
});

test("a new pull layer resets progress to zero and terminal events remove tracking", () => {
    const name = "zero-progress-test";
    const errors: string[] = [];
    let successes = 0;
    const receive = updateProgressCallback({
        onSuccess: () => {
            successes++;
        },
        onError: (message) => errors.push(message),
    });
    try {
        getOllamaModelStore().upsertPullingModel({ name, isTracking: true, progress: 95 });
        receive({ model: name, status: "downloading", percent: 0 });
        assert.equal(getOllamaModelStore().pullingModels[name]?.progress, 0);
        receive({ model: name, status: "error", error: "Preserved diagnostic" });
        assert.equal(getOllamaModelStore().pullingModels[name], undefined);
        assert.deepEqual(errors, ["Preserved diagnostic"]);
        receive({ model: name, status: "downloading", percent: 10 });
        receive({ model: name, status: "success" });
        assert.equal(getOllamaModelStore().pullingModels[name], undefined);
        assert.equal(successes, 1);
    } finally {
        getOllamaModelStore().deletePullingModel(name);
    }
});
