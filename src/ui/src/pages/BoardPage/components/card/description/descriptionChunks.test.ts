import assert from "node:assert/strict";
import test from "node:test";

import { buildContentBlockChunks, buildDescriptionChunks, getMarkdownNodeText } from "./descriptionChunks.ts";
import type { IContentBlock } from "@/core/models/ProjectCard";

test("buildDescriptionChunks starts each heading and keeps the original markdown", () => {
    const content = "# First\n\nalpha\n\nparagraph\n\n# Second\n\nbeta";
    const chunks = buildDescriptionChunks(content);

    assert.equal(chunks[0].metadata.type, "heading");
    assert.equal(chunks[0].metadata.heading, "First");
    assert.equal(chunks[1].metadata.type, "mixed");
    assert.equal(chunks[2].metadata.type, "heading");
    assert.equal(chunks[2].metadata.heading, "Second");
    const serialized = chunks
        .map((chunk) => chunk.content)
        .join("")
        .trim();
    for (const phrase of ["First", "alpha", "paragraph", "Second", "beta"]) {
        assert.match(serialized, new RegExp(phrase));
    }
});

test("heavy markdown blocks are isolated for predictable virtualization", () => {
    const list = Array.from({ length: 6 }, (_, index) => `${index + 1}. item`).join("\n");
    const content = `intro\n\n${list}\n\n| Left | Right |\n| --- | --- |\n| a | b |`;
    const chunks = buildDescriptionChunks(content);

    assert.equal(chunks.length, 3);
    assert.equal(chunks[0].metadata.type, "paragraph");
    assert.equal(chunks[1].metadata.type, "list");
    assert.equal(chunks[1].metadata.isHeavy, true);
    assert.equal(chunks[2].metadata.type, "table");
    assert.equal(chunks[2].metadata.isHeavy, true);
});

test("preview text is bounded without loading rendered content", () => {
    const content = `# Long section\n\n${"preview ".repeat(40)}`;
    const chunks = buildDescriptionChunks(content);

    assert.equal(chunks[1].metadata.previewText.length, 181);
    assert.equal(chunks[1].metadata.previewText.endsWith("…"), true);
    assert.equal(getMarkdownNodeText({ type: "text", value: "hello" }), "hello");
});

test("structured windows split long rich text while retaining code/diagram literals and stable ownership", () => {
    const rich: IContentBlock = {
        block_uid: "rich",
        type: "rich_text",
        order: 0,
        revision: 1,
        updated_at: null,
        payload: { text: Array.from({ length: 30 }, (_, index) => `## Section ${index}\n\nParagraph ${index}`).join("\n\n") },
    };
    const code: IContentBlock = {
        ...rich,
        block_uid: "code",
        type: "code",
        order: 1,
        payload: { language: "text", source: "## literal\n**data**\n```" },
    };
    const diagram: IContentBlock = {
        ...rich,
        block_uid: "diagram",
        type: "diagram",
        order: 2,
        payload: { engine: "mermaid", source: "graph TD; A-->B", view_mode: "source" },
    };
    const blocks = [diagram, rich, code];
    const before = JSON.stringify(blocks);
    const chunks = buildContentBlockChunks(blocks);
    assert.equal(chunks.length, 62);
    assert.equal(chunks[0].metadata.heading, "Section 0");
    assert.equal(chunks[58].metadata.heading, "Section 29");
    assert.equal(chunks[60].block, code);
    assert.equal(chunks[60].content, code.payload.source);
    assert.equal(chunks[61].block, diagram);
    assert.equal(chunks[61].content, diagram.payload.source);
    assert.equal(new Set(chunks.map((chunk) => chunk.id)).size, chunks.length);
    assert.deepEqual(
        buildContentBlockChunks(blocks).map((chunk) => chunk.id),
        chunks.map((chunk) => chunk.id)
    );
    assert.equal(JSON.stringify(blocks), before);
});
