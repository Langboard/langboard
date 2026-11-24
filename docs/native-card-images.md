# Native card images

`read_card_images` returns MCP `ImageContent` plus a structured inclusion/omission manifest. It is available in Agent/Core and native OAuth; legacy explicit grants remain required for API-key clients. Existing `read_card_attachment` is unchanged.

The tool validates project/card ancestry and Read permission, finds Markdown/HTML/editor image references in the card body, and accepts up to 25 explicit attachment UIDs. It verifies each selected attachment belongs to that card. Body code and ordinary links are ignored. `include_body_images=false` reads only the selected attachments.

Native storage reads replace plugin HTTP retrieval. Only Langboard origin `/file/.../card_attachment/...` references are accepted. No external URL, redirect, bearer-token forwarding, avatar/wrong storage namespace, query credential or path traversal is allowed. External images are reported as unsupported sources. This scope does not claim parity for externally hosted/CDN images.

Downloads stop at 25 MiB total before unbounded storage allocation; results include at most ten images. PNG/JPEG/GIF/WebP signatures identify image MIME instead of trusting filename extensions. This is signature detection, not full image decoding or malware inspection. Missing content, unsupported source/type and limits produce omission reasons. Documents are not extracted.

Modern response budgeting permits up to 36 MiB serialized output for this bounded image tool when it contains image blocks, covering base64 expansion. Other read responses retain the existing budget. These limits are not removed by Raw proxy calls.

## Body image selection

When a card has content blocks, only rich-text blocks contribute body image
references. Code and diagram blocks remain literal, and an older description is
not used as a fallback. Cards without blocks use their stored description.
Attachment-only reads do not inspect body content. Reading an image does not
extract document text or grant access to another card's attachment.
