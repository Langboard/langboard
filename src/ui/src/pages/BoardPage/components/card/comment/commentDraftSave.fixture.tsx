import React, { createContext, useContext, useRef, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import BoardCommentForm from "./BoardCommentForm";
import BoardComment from "./BoardComment";
import { getEditorStore } from "@/core/stores/EditorStore";
import { getCardCommentDraftStore } from "@/core/stores/CardCommentDraftStore";
import { getCardDraftStore } from "@/core/stores/CardDraftStore";
import type { IEditorContent } from "@/core/models/Base";

type Props = Record<string, unknown> & { children?: React.ReactNode };
export function Box({ children, onClick, onKeyDown, role, tabIndex }: Props) {
    return (
        <div
            onClick={onClick as React.MouseEventHandler}
            onKeyDown={onKeyDown as React.KeyboardEventHandler}
            role={role as string}
            tabIndex={tabIndex as number}
        >
            {children}
        </div>
    );
}
export function Button({ children, onClick, disabled, isValidating }: Props) {
    return (
        <button type="button" onClick={onClick as React.MouseEventHandler} disabled={Boolean(disabled || isValidating)}>
            {children}
        </button>
    );
}
export const Form = { Root: Box };
const DrawerContext = createContext(false);
export const Drawer = {
    Root: ({ children, open }: Props) => <DrawerContext.Provider value={Boolean(open)}>{children}</DrawerContext.Provider>,
    Content: ({ children }: Props) => (useContext(DrawerContext) ? <div>{children}</div> : null),
    Title: () => null,
    Handle: Box,
};
export const UserAvatar = { Root: () => null };
export const Toast = { Add: { success: () => {}, error: () => {}, promise: () => {} } };
export const useTranslation = () => [(text: string) => text];
export const useNativeImageComments = () => ({ isCurrent: () => true });
export const useToggleEditingByClickOutside = () => ({ stopEditing: () => {} });
export const setupApiErrorHandler = () => ({ handle: () => {} });
export const cn = (...values: unknown[]) => values.filter(Boolean).join(" ");
export function getMentionOnSelectItem() {
    return () => {};
}
export const EEditorType = { CardComment: "edit", CardNewComment: "new" };
export const Empty = () => null;
export const resolveCardCommentAnchorElement = () => null;
export const isModel = () => true;
const bots = [{ uid: "bot", name: "Test bot" }];
export const BotModel = { Model: { useModels: () => bots } };
export const ProjectCard = { Model: { useModels: () => [] } };
const CommentContext = createContext<unknown>(null);
export const ModelRegistry = {
    ProjectCardComment: {
        Provider: ({ model, params, children }: Props) => <CommentContext.Provider value={{ model, params }}>{children}</CommentContext.Provider>,
        useContext: () => useContext(CommentContext),
    },
};
export const ProjectRole = { EAction: { Read: "read" } };
// Component-boundary mock: exposes candidate props, not the native Plate mention popup.
export function PlateEditor({
    value,
    setValue,
    readOnly,
    mentionables,
}: {
    value: IEditorContent;
    setValue: (value: IEditorContent) => void;
    readOnly?: boolean;
    mentionables: { uid: string }[];
}) {
    const [text, setText] = useState(value.content);
    return (
        <textarea
            aria-label="Comment content"
            data-mention-candidates={JSON.stringify(mentionables.map(({ uid }) => uid))}
            value={text}
            readOnly={readOnly}
            onChange={(event) => {
                setText(event.target.value);
                setValue({ content: event.target.value });
            }}
        />
    );
}
type Callbacks = {
    onSuccess: (data?: { updated_at: Date; content_revision: string }) => void;
    onError: (error: Error) => void;
    onSettled: () => void;
};
let pending: Callbacks | undefined;
let submitted = "";
let submittedRevision: string | undefined;
let submittedRequestUID: string | undefined;
export const useMutation = () => ({
    mutate: (data: { content: IEditorContent; request_uid?: string; expected_revision?: string }, callbacks: Callbacks) => {
        submitted = data.content.content;
        submittedRevision = data.expected_revision;
        submittedRequestUID = data.request_uid;
        pending = callbacks;
    },
});
const user = { uid: "user", firstname: "Test", lastname: "User", useField: () => false, isValidUser: () => true, isDeletedUser: () => false };
const projectMembers = [user, { ...user, uid: "other-user" }];
let visibility = new URLSearchParams(location.search).get("visibility") ?? "SHARED";
const visibilityListeners = new Set<() => void>();
const card = {
    uid: "card",
    useField: () =>
        useSyncExternalStore(
            (listener) => {
                visibilityListeners.add(listener);
                return () => visibilityListeners.delete(listener);
            },
            () => visibility
        ),
    useForeignFieldArray: () => projectMembers,
};
const commentListeners = new Set<() => void>();
const comment = {
    uid: "comment",
    content: { content: "Saved original" },
    updated_at: new Date(0),
    content_revision: "revision-1",
    useField(field: string): unknown {
        return useSyncExternalStore(
            (listener) => {
                commentListeners.add(listener);
                return () => commentListeners.delete(listener);
            },
            () =>
                field === "content"
                    ? this.content
                    : field === "updated_at"
                      ? this.updated_at
                      : field === "content_revision"
                        ? this.content_revision
                        : null
        );
    },
    useForeignFieldOne: () => user,
};
const replyRef = { current: () => {} };
const anchoredCommentRef = { current: () => {} };
const board = { projectUID: "project", card, currentUser: user, replyRef, anchoredCommentRef, hasRoleAction: () => true };
export const useBoardCard = () => board;
const mode = new URLSearchParams(location.search).get("mode") ?? "panel";
if (new URLSearchParams(location.search).has("image")) {
    getCardCommentDraftStore().saveSnapshot("user", "project", "card", {
        content: "",
        request_uid: "submitted-image-request",
        anchor: {
            type: "ImageRegionSelector",
            version: 1,
            source: { type: "card_attachment", attachment_uid: "attachment", revision: "revision" },
            shape: "point",
            image_width: 100,
            image_height: 100,
            x: 0,
            y: 0,
            width: 0,
            height: 0,
        },
    });
}
export const useBoardCardPanel = () => ({
    isCommentPanelOpen: true,
    setIsCommentPanelOpen: () => {},
    commentLayoutMode: mode === "mobile" ? "mobile" : "panel",
    commentSurfaceRef: { current: null },
});
function Fixture() {
    const [mounted, setMounted] = useState(true);
    const version = useRef(0);
    return (
        <main>
            {["PRIVATE", "INTERNAL", "SHARED"].map((nextVisibility) => (
                <button
                    key={nextVisibility}
                    onClick={() => {
                        visibility = nextVisibility;
                        visibilityListeners.forEach((listener) => listener());
                    }}
                >
                    Set visibility {nextVisibility}
                </button>
            ))}
            <button
                onClick={() => {
                    const callbacks = pending;
                    pending = undefined;
                    callbacks?.onSuccess({ updated_at: new Date(1), content_revision: "revision-saved" });
                    commentListeners.forEach((listener) => listener());
                    callbacks?.onSettled();
                }}
            >
                Resolve success
            </button>
            <button
                onClick={() => {
                    const callbacks = pending;
                    pending = undefined;
                    callbacks?.onError(new Error("Save failed"));
                    callbacks?.onSettled();
                }}
            >
                Resolve failure
            </button>
            <button
                onClick={() => {
                    comment.content = { content: "Saved remote update" };
                    comment.updated_at = new Date(2);
                    comment.content_revision = "revision-remote";
                    commentListeners.forEach((listener) => listener());
                }}
            >
                Remote update
            </button>
            <button
                onClick={() => {
                    const callbacks = pending;
                    pending = undefined;
                    callbacks?.onError(Object.assign(new Error("Conflict"), { response: { status: 409 } }));
                    callbacks?.onSettled();
                }}
            >
                Resolve conflict
            </button>
            <button onClick={() => getEditorStore().setCurrentEditor("other-editor")}>Switch editor</button>
            <button
                onClick={() => {
                    version.current++;
                    setMounted(false);
                    setTimeout(() => setMounted(true), 0);
                }}
            >
                Remount composer
            </button>
            <button
                onClick={() => {
                    document.querySelector("output")!.textContent = JSON.stringify({
                        submitted,
                        submittedRevision,
                        serverContent: comment.content.content,
                        submittedRequestUID,
                        snapshot: getCardCommentDraftStore().getSnapshot("user", "project", "card"),
                        currentEditor: getEditorStore().currentEditor,
                        draft:
                            mode === "edit"
                                ? (getCardDraftStore().getDraft({ userUID: "user", projectUID: "project", kind: "comment-edit", scopeUID: "comment" })
                                      ?.content ?? "")
                                : getCardCommentDraftStore().getDraft("user", "project", "card"),
                    });
                }}
            >
                Inspect draft
            </button>
            <output />
            {mounted &&
                (mode === "edit" ? (
                    <BoardComment key={version.current} comment={comment as never} deletedComment={() => {}} />
                ) : (
                    <BoardCommentForm key={version.current} variant={mode as "panel" | "mobile"} />
                ))}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
