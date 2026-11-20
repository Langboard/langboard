import assert from "node:assert/strict";
import test from "node:test";
import { createStoryboardResource } from "./createStoryboardResource.ts";

const vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nstoryboard.jpg#xywh=0,0,160,90\n";

test("authorized sprite and VTT are scoped to revocable same-page blobs", async () => {
    const resource = createStoryboardResource({ available: true, vtt, sprite_base64: "/9j/2Q==" });
    const content = await resource.blob.text();
    assert.equal(resource.blob.type, "text/vtt");
    assert.match(content, /blob:[^\s]+#xywh=0,0,160,90/);
    const spriteURL = content.split("\n")[3].split("#")[0];
    assert.equal((await fetch(spriteURL)).headers.get("content-type"), "image/jpeg");
    resource.dispose();
    await assert.rejects(fetch(spriteURL));
});

test("missing, invalid or oversized previews do not become image resources", () => {
    for (const payload of [
        { available: false },
        { available: true, vtt, sprite_base64: "not base64" },
        { available: true, vtt, sprite_base64: btoa("not a JPEG") },
        { available: true, vtt: "not WebVTT", sprite_base64: "/9j/2Q==" },
        { available: true, vtt: "WEBVTT\n" + "x".repeat(32_768), sprite_base64: "/9j/2Q==" },
    ])
        assert.throws(() => createStoryboardResource(payload));
});
