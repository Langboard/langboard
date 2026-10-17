import { NodeApi, type TCaptionProps, type TImageElement } from "platejs";

/** Explicit empty alt marks a decorative image; captions are the metadata fallback. */
export function getImageAlt(element: TImageElement & TCaptionProps): string {
    if (typeof element.alt === "string") return element.alt;
    return element.caption?.map((node) => NodeApi.string(node)).join(" ") ?? "";
}
