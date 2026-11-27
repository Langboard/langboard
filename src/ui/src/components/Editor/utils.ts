import type { IEditorContent } from "@/core/models/Base";

const INVISIBLE_EDITOR_TEXT_PATTERN = /[\u200B-\u200D\uFEFF]/g;

export const sanitizeEditorContent = (content: string) => {
    return content.replace(INVISIBLE_EDITOR_TEXT_PATTERN, "").trim();
};

export const sanitizeEditorValue = <TValue extends IEditorContent>(value: TValue): TValue => {
    return {
        ...value,
        content: sanitizeEditorContent(value.content),
    };
};

export const isEmptyEditorContent = (content: string) => {
    return sanitizeEditorContent(content).length === 0;
};
