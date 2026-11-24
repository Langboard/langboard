import assert from "node:assert/strict";
import test from "node:test";

import { buildDescriptionChunks } from "./descriptionChunks.ts";
import {
    buildRailMarkers,
    getMarkerOpacity,
    getMarkerWidth,
    getNearestMarkerIndex,
    getRailGeometry,
    MAX_RAIL_MARKERS,
} from "./descriptionOverviewRailData.ts";

function createChunks(count: number) {
    return buildDescriptionChunks(Array.from({ length: count }, (_, index) => `# Heading ${index}\n\ncontent ${index}`).join("\n\n"));
}

test("rail preserves chunk indexes up to the marker limit", () => {
    const chunks = createChunks(12);
    const markers = buildRailMarkers(chunks);

    assert.equal(markers.length, chunks.length);
    assert.deepEqual(
        markers.map((marker) => marker.index),
        chunks.map((_, index) => index)
    );
});

test("oversized documents are bucketed while preferring headings", () => {
    const chunks = createChunks(MAX_RAIL_MARKERS * 2);
    const markers = buildRailMarkers(chunks);

    assert.equal(markers.length, MAX_RAIL_MARKERS);
    markers.forEach((marker, bucketIndex) => {
        assert.equal(chunks[marker.index].metadata.type, "heading");
        const bucketSize = Math.ceil(chunks.length / MAX_RAIL_MARKERS);
        assert.deepEqual(marker.range, {
            start: bucketIndex * bucketSize + 1,
            end: Math.min(chunks.length, (bucketIndex + 1) * bucketSize),
        });
    });
});

test("active marker metrics form a compact distance wave", () => {
    assert.equal(getMarkerWidth(0), "w-5");
    assert.equal(getMarkerWidth(4), "w-1.5");
    assert.equal(getMarkerOpacity(0), "opacity-100");
    assert.equal(getMarkerOpacity(8), "opacity-30");
    assert.equal(getNearestMarkerIndex(buildRailMarkers(createChunks(20)), 9), 9);
});

test("marker density follows available rail space and preserves document coverage", () => {
    const chunks = createChunks(80);
    const markers = buildRailMarkers(chunks, 12);
    assert.ok(markers.length <= 12);
    assert.equal(markers[0].range?.start, 1);
    assert.equal(markers.at(-1)?.range?.end, chunks.length);
});

test("native scroll geometry hides empty decoration and keeps thumb proportional", () => {
    assert.equal(getRailGeometry(400, 400, 0).visible, false);
    assert.equal(getRailGeometry(400, 401, 0).visible, false);
    const medium = getRailGeometry(400, 800, 200);
    assert.equal(medium.thumbHeight, medium.height / 2);
    assert.equal(medium.thumbTop, (medium.height - medium.thumbHeight) / 2);
    const long = getRailGeometry(400, 8000, 7600);
    assert.equal(long.thumbHeight, 20);
    assert.equal(long.thumbTop + long.thumbHeight, long.height);
    assert.equal(getRailGeometry(20, 8000, 0).visible, false);
    assert.equal(getRailGeometry(400, 800, -80).thumbTop, 0);
});
