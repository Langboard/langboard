export interface AttachmentStoryboard {
    available: boolean;
    vtt?: string;
    sprite_base64?: string;
}

/** Convert authorized API assets into same-page resources consumed by the OSS skin. */
export function createStoryboardResource(data: AttachmentStoryboard) {
    if (
        !data.available ||
        typeof data.vtt !== "string" ||
        typeof data.sprite_base64 !== "string" ||
        !data.vtt.startsWith("WEBVTT\n") ||
        data.vtt.length > 32_768 ||
        data.sprite_base64.length > 1_500_000
    ) {
        throw new Error("Attachment storyboard unavailable");
    }
    const bytes = Uint8Array.from(atob(data.sprite_base64), (character) => character.charCodeAt(0));
    if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error("Attachment storyboard is not a JPEG sprite");
    const sprite = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
    return {
        blob: new Blob([data.vtt.replaceAll("storyboard.jpg", sprite)], { type: "text/vtt" }),
        dispose: () => URL.revokeObjectURL(sprite),
    };
}
