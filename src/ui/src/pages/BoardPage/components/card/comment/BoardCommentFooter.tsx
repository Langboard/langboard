import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Separator from "@/components/base/Separator";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import { isEmptyEditorContent, sanitizeEditorContent, sanitizeEditorValue } from "@/components/Editor/utils";
import useDeleteCardComment from "@/controllers/api/card/comment/useDeleteCardComment";
import useUpdateCardComment from "@/controllers/api/card/comment/useUpdateCardComment";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { IEditorContent } from "@/core/models/Base";
import { isModel, ModelRegistry } from "@/core/models/ModelRegistry";
import { ProjectRole } from "@/core/models/roles";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { getCardDraftStore } from "@/core/stores/CardDraftStore";
import { getEditorStore } from "@/core/stores/EditorStore";
import BoardCommentSeen from "./BoardCommentSeen";
import BoardCommentReaction from "@/pages/BoardPage/components/card/comment/BoardCommentReaction";
import { IBoardCommentContextParams } from "@/pages/BoardPage/components/card/comment/types";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

function BoardCommentFooter(): React.JSX.Element {
    const { params } = ModelRegistry.ProjectCardComment.useContext<IBoardCommentContextParams>();
    const { isCurrentEditor } = params;

    return (
        <Flex items="center" gap="2" wrap={true}>
            {isCurrentEditor ? <BoardCommentFooterEditButtons /> : <BoardCommentFooterActions />}
        </Flex>
    );
}

function BoardCommentFooterEditButtons() {
    const { projectUID, card, currentUser } = useBoardCard();
    const [t] = useTranslation();
    const { model: comment, params } = ModelRegistry.ProjectCardComment.useContext<IBoardCommentContextParams>();
    const { valueRef, flushDraftAutosave, cancelDraftAutosave, editBaseRef, hasServerConflict, markSaveConflict } = params;
    const saveSessionRef = useRef(0);
    useEffect(() => {
        saveSessionRef.current++;
        return () => {
            saveSessionRef.current++;
        };
    }, [currentUser.uid, projectUID, card.uid, comment.uid]);
    const [isValidating, setIsValidating] = useState(false);
    const setValue = (value: IEditorContent) => {
        valueRef.current = value;
    };
    const { mutate: updateCommentMutate } = useUpdateCardComment();

    const cancelEditing = () => {
        setValue(comment.content);
        cancelDraftAutosave();
        getEditorStore().setCurrentEditor(null);
    };

    const saveComment = () => {
        if (isValidating || hasServerConflict) {
            return;
        }

        setIsValidating(true);

        // Persist any debounce-pending edit before serializing, so a failed
        // submit still leaves the latest content recoverable.
        flushDraftAutosave();

        const submittedValue = valueRef.current;
        const saveSession = saveSessionRef.current;
        const content = sanitizeEditorValue(submittedValue);

        if (isEmptyEditorContent(content.content)) {
            Toast.Add.error(t("card.errors.Comment content cannot be empty."));
            setIsValidating(false);
            return;
        }

        if (sanitizeEditorContent(comment.content.content) === content.content) {
            cancelDraftAutosave();
            setIsValidating(false);
            return;
        }

        const submittedScope = { userUID: currentUser.uid, projectUID, kind: "comment-edit" as const, scopeUID: comment.uid };
        // Capture a durable revision even when an unchanged restored draft has no cached copy.
        getCardDraftStore().saveDraft(submittedScope, content.content, {
            base_content: editBaseRef.current.content.content,
            base_revision: editBaseRef.current.revision,
            base_updated_at: editBaseRef.current.updatedAt.toISOString(),
        });
        const submittedDraft = getCardDraftStore().getDraft(submittedScope);

        updateCommentMutate(
            {
                project_uid: projectUID,
                card_uid: card.uid,
                comment_uid: comment.uid,
                content,
                expected_revision: editBaseRef.current.revision,
            },
            {
                onSuccess: (saved) => {
                    // A newer socket update can arrive after this write committed
                    // but before its response. Never replace that newer projection.
                    if (
                        (comment.content_revision &&
                            editBaseRef.current.revision &&
                            comment.content_revision !== editBaseRef.current.revision &&
                            comment.content_revision !== saved?.content_revision) ||
                        (comment.content.content !== editBaseRef.current.content.content && comment.content.content !== content.content)
                    ) {
                        markSaveConflict();
                        return;
                    }
                    comment.content = content;
                    if (saved?.updated_at) comment.updated_at = saved.updated_at;
                    comment.content_revision = saved?.content_revision;
                    editBaseRef.current = { content, revision: saved?.content_revision, updatedAt: comment.updated_at };
                    if (valueRef.current !== submittedValue) {
                        getCardDraftStore().saveDraft(
                            { userUID: currentUser.uid, projectUID, kind: "comment-edit", scopeUID: comment.uid },
                            valueRef.current.content,
                            {
                                base_content: content.content,
                                base_revision: saved?.content_revision,
                                base_updated_at: comment.updated_at.toISOString(),
                            }
                        );
                    }
                    // Acknowledge the submitted revision after navigation, but preserve a reopened editor session.
                    const isSubmittedDraft =
                        submittedDraft !== null &&
                        getCardDraftStore().getDraft(submittedScope) === submittedDraft &&
                        (saveSessionRef.current === saveSession || getEditorStore().currentEditor !== params.editorName);
                    if (isSubmittedDraft) getCardDraftStore().clearDraft(submittedScope);
                    Toast.Add.success(t("successes.Comment updated successfully."));
                    if (isSubmittedDraft && saveSessionRef.current === saveSession && valueRef.current === submittedValue) {
                        cancelEditing();
                    }
                },
                onError: (error) => {
                    if ((error as { response?: { status?: number } }).response?.status === 409) markSaveConflict();
                    const { handle } = setupApiErrorHandler({});

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    return (
        <>
            <Button variant="secondary" onClick={cancelEditing} disabled={isValidating}>
                {t("common.Cancel")}
            </Button>
            <SubmitButton type="button" onClick={saveComment} isValidating={isValidating} disabled={hasServerConflict}>
                {t("common.Save")}
            </SubmitButton>
        </>
    );
}

function BoardCommentFooterActions() {
    const { projectUID, card, currentUser, hasRoleAction, replyRef } = useBoardCard();
    const [t] = useTranslation();
    const { model: comment, params } = ModelRegistry.ProjectCardComment.useContext<IBoardCommentContextParams>();
    const { author, deletedComment, beginEditing } = params;
    const isAdmin = currentUser.useField("is_admin");
    const [isValidating, setIsValidating] = useState(false);
    const canEdit = currentUser.uid === author.uid || isAdmin;
    const visibility = card.useField("visibility");
    const canReply = !isModel(author, "User") || author.isValidUser();
    const { mutateAsync: deleteCommentMutateAsync } = useDeleteCardComment({ interceptToast: true });

    const deleteComment = () => {
        if (isValidating) {
            return;
        }

        setIsValidating(true);

        const promise = deleteCommentMutateAsync({
            project_uid: projectUID,
            card_uid: card.uid,
            comment_uid: comment.uid,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Deleting..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                deletedComment(comment.uid);
                return t("successes.Comment deleted successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <>
            <BoardCommentReaction comment={comment} />
            {visibility !== "PRIVATE" && currentUser.isValidUser() && <BoardCommentSeen comment={comment} />}
            {visibility !== "PRIVATE" &&
                hasRoleAction(ProjectRole.EAction.Read) &&
                currentUser.uid !== author.uid &&
                currentUser.isValidUser() &&
                canReply && (
                    <>
                        <Separator orientation="vertical" className="h-1/2" />
                        <Button
                            variant="link"
                            size="sm"
                            data-reply-component
                            className="h-5 p-0 text-accent-foreground/50"
                            onClick={() => replyRef.current?.(author)}
                        >
                            {t("card.Reply")}
                        </Button>
                    </>
                )}
            {canEdit && (
                <>
                    <Separator orientation="vertical" className="h-1/2" />
                    <Button
                        variant="link"
                        size="sm"
                        className="h-5 p-0 text-accent-foreground/50"
                        data-comment-edit
                        onClick={beginEditing}
                        disabled={isValidating}
                    >
                        {t("common.Edit")}
                    </Button>
                    <Separator orientation="vertical" className="h-1/2" />
                    <Button variant="link" size="sm" className="h-5 p-0 text-accent-foreground/50" onClick={deleteComment} disabled={isValidating}>
                        {t("common.Delete")}
                    </Button>
                </>
            )}
        </>
    );
}

export default BoardCommentFooter;
