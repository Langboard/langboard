import { Utils } from "@langboard/core/utils";
import invariant from "tiny-invariant";

export function renderClonedPreview(outer: HTMLDivElement, container: HTMLElement) {
    const rect = outer.getBoundingClientRect();
    const preview = outer.cloneNode(true);
    invariant(Utils.Type.isElement(preview, "div"));
    preview.style.width = `${rect.width}px`;
    preview.style.height = `${rect.height}px`;
    container.appendChild(preview);
}
