import { MarkdownPlugin } from "@platejs/markdown";
import { createSlateEditor } from "platejs";
import { memo, useMemo } from "react";
import { BaseEditorKit } from "@/components/Editor/editor-base-kit";
import { BaseInternalLinkKit } from "@/components/Editor/plugins/internal-link-base-kit";
import { EditorStatic } from "@/components/plate-ui/editor-static";

const StaticMarkdownKit = [...BaseEditorKit, ...BaseInternalLinkKit];

export const StaticMarkdownContent = memo(({ content }: { content: string }): React.JSX.Element => {
    const editor = useMemo(() => createSlateEditor({ plugins: StaticMarkdownKit }), []);
    const value = useMemo(() => editor.getApi(MarkdownPlugin).markdown.deserialize(content), [content, editor]);

    return <EditorStatic editor={editor} value={value} variant="ai" className="h-full min-h-0 px-0" />;
});
