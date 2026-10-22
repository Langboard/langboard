import { NativeImageContext, type NativeImageComments } from "./NativeImageCommentContext";
import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { ProjectCardAttachment, ProjectCardComment } from "@/core/models";
import type { ICardCommentAnchor, ICardCommentImageAnchor } from "@/core/models/types/card-comment-anchor.type";
import { isImageCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
import type { ImageAnnotationContext } from "./ImageAnnotations";
import ImagePreviewDialog from ".";
import Button from "@/components/base/Button";
import { useTranslation } from "react-i18next";

function AttachmentSubscription({ attachment, changed }: { attachment: ProjectCardAttachment.TModel; changed: () => void }) {
    const revision = attachment.useField("image_revision");
    const url = attachment.useField("url");
    const name = attachment.useField("name");
    useEffect(changed, [revision, url, name, changed]);
    return null;
}

type Snapshot = ImageAnnotationContext["comments"][number];
function Subscription({ comment, update }: { comment: ProjectCardComment.TModel; update: (uid: string, snapshot: Snapshot | null) => void }) {
    const anchor = comment.useField("anchor");
    const content = comment.useField("content");
    useEffect(() => {
        update(comment.uid, isImageCommentAnchor(anchor) ? { uid: comment.uid, anchor, preview: content.content.slice(0, 240) } : null);
    }, [anchor, content, comment.uid, update]);
    useEffect(() => () => update(comment.uid, null), [comment.uid, update]);
    return null;
}
export function NativeImageCommentProvider({
    cardUID,
    anchoredCommentRef,
    surfaceRef,
    openComments,
    children,
}: {
    cardUID: string;
    anchoredCommentRef: React.RefObject<(anchor: ICardCommentAnchor) => void>;
    surfaceRef: React.RefObject<HTMLDivElement | null>;
    openComments: () => void;
    children: React.ReactNode;
}) {
    const [t] = useTranslation();
    const attachments = ProjectCardAttachment.Model.useModels((model) => model.card_uid === cardUID, [cardUID]);
    const comments = ProjectCardComment.Model.useModels((model) => model.card_uid === cardUID, [cardUID]);
    const [, refresh] = useState(0);
    const changed = useCallback(() => refresh((value) => value + 1), []);
    const [snapshots, setSnapshots] = useState<Record<string, Snapshot>>({});
    const [selection, setSelection] = useState<{ anchor: ICardCommentImageAnchor; commentUID: string } | null>(null);
    const update = useCallback(
        (uid: string, snapshot: Snapshot | null) =>
            setSnapshots((previous) => {
                const next = { ...previous };
                if (snapshot) next[uid] = snapshot;
                else delete next[uid];
                return next;
            }),
        []
    );
    const isCurrent = (anchor: ICardCommentImageAnchor) =>
        attachments.some((attachment) => attachment.uid === anchor.source.attachment_uid && attachment.image_revision === anchor.source.revision);
    const contextForURL: NativeImageComments["contextForURL"] = (url, close) => {
        const matches = attachments.filter((attachment) => attachment.url === url && /^[0-9a-f]{64}$/.test(attachment.image_revision ?? ""));
        if (matches.length !== 1) return undefined;
        const attachment = matches[0];
        return {
            source: { type: "card_attachment", attachment_uid: attachment.uid, revision: attachment.image_revision! },
            comments: Object.values(snapshots).filter(({ anchor }) => anchor.source.attachment_uid === attachment.uid),
            highlightedCommentUID: selection?.commentUID,
            onCreate: (anchor) => {
                if (!isCurrent(anchor)) return;
                anchoredCommentRef.current(anchor);
                close();
            },
            onOpenComment: (uid) => {
                close();
                openComments();
                window.setTimeout(() => {
                    const comment = surfaceRef.current?.querySelector<HTMLElement>(`[data-card-comment-uid="${CSS.escape(uid)}"]`);
                    comment?.scrollIntoView({ behavior: "smooth", block: "center" });
                    comment?.querySelector<HTMLElement>("[data-comment-edit]")?.focus();
                }, 100);
            },
        };
    };
    const attachment = selection && attachments.find((item) => item.uid === selection.anchor.source.attachment_uid);
    return (
        <NativeImageContext.Provider value={{ contextForURL, jump: (anchor, commentUID) => setSelection({ anchor, commentUID }), isCurrent }}>
            {attachments.map((attachment) => (
                <AttachmentSubscription key={attachment.uid} attachment={attachment} changed={changed} />
            ))}
            {comments.map((comment) => (
                <Subscription key={comment.uid} comment={comment} update={update} />
            ))}
            {children}
            {selection &&
                createPortal(
                    attachment && isCurrent(selection.anchor) ? (
                        <ImagePreviewDialog
                            files={[{ name: attachment.name, url: attachment.url }]}
                            initialIndex={0}
                            onClose={() => setSelection(null)}
                        />
                    ) : (
                        <div
                            role="alertdialog"
                            aria-label={t("card.Image unavailable")}
                            className="fixed inset-0 z-50 flex items-center justify-center bg-background/95"
                        >
                            <div>
                                <p>{t("card.Image changed or missing")}</p>
                                <Button onClick={() => setSelection(null)}>{t("common.Close")}</Button>
                            </div>
                        </div>
                    ),
                    document.body
                )}
        </NativeImageContext.Provider>
    );
}
