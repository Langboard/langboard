import { useNativeImageComments } from "@/components/ImagePreviewDialog/NativeImageCommentContext";
import { isImageCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
import { useBoardCardPanel } from "@/core/providers/BoardCardProvider";
import Button from "@/components/base/Button";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Skeleton from "@/components/base/Skeleton";
import DateDistance from "@/components/DateDistance";
import { TEditor } from "@/components/Editor/editor-kit";
import { PlateEditor } from "@/components/Editor/plate-editor";
import UserAvatar from "@/components/UserAvatar";
import UserAvatarDefaultList from "@/components/UserAvatarDefaultList";
import UserLikeComponent from "@/components/UserLikeComponent";
import { BotModel, ProjectCard, ProjectCardComment, User } from "@/core/models";
import { IEditorContent } from "@/core/models/Base";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { getCardDraftStore, type IDraftPayload, type IDraftScope } from "@/core/stores/CardDraftStore";
import { getEditorStore, useIsCurrentEditor } from "@/core/stores/EditorStore";
import { cn } from "@/core/utils/ComponentUtils";
import BoardCommentFooter from "@/pages/BoardPage/components/card/comment/BoardCommentFooter";
import { IBoardCommentContextParams } from "@/pages/BoardPage/components/card/comment/types";
import { EEditorType } from "@langboard/core/constants";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { resolveCardCommentAnchorElement } from "@/pages/BoardPage/components/card/comment/commentAnchor";

export function SkeletonBoardComment({ ref }: { ref?: React.Ref<HTMLDivElement> }): React.JSX.Element {
    return (
        <Box mt="4" display="grid" gap="2" className="grid-cols-[theme(spacing.8),1fr]" ref={ref}>
            <Skeleton size="8" rounded="full" />
            <Flex direction="col" gap="1">
                <Flex gap="1" items="center">
                    <Skeleton h="5" w="24" />
                    <Skeleton h="4" w="36" />
                </Flex>
                <Skeleton h="12" rounded="lg" className="w-1/2 rounded-ss-none" />
            </Flex>
        </Box>
    );
}

export interface IBoardCommentProps {
    comment: ProjectCardComment.TModel;
    deletedComment: (commentUID: string) => void;
}

const BoardComment = memo(({ comment, deletedComment }: IBoardCommentProps): React.JSX.Element => {
    const { projectUID, card, currentUser } = useBoardCard();
    const nativeImages = useNativeImageComments();
    const { commentSurfaceRef } = useBoardCardPanel();
    const [t] = useTranslation();
    const editorName = `${card.uid}-comment-${comment.uid}`;
    const isCurrentEditor = useIsCurrentEditor(editorName);
    const projectMembers = card.useForeignFieldArray("project_members");
    const visibility = card.useField("visibility");
    const editorRef = useRef<TEditor>(null);
    const bots = BotModel.Model.useModels(() => true);
    const mentionables = useMemo(() => (visibility === "PRIVATE" ? [] : [...projectMembers, ...bots]), [visibility, projectMembers, bots]);
    const cards = ProjectCard.Model.useModels((model) => model.uid !== card.uid && model.project_uid === projectUID, [projectUID, card]);
    const content = comment.useField("content");
    const anchor = comment.useField("anchor");
    const updatedAt = comment.useField("updated_at");
    const revision = comment.useField("content_revision");
    const commentUser = comment.useForeignFieldOne("user");
    const commentBot = comment.useForeignFieldOne("bot");
    const commentAuthor = commentUser || commentBot;
    const editDraft = isCurrentEditor
        ? getCardDraftStore().getDraft({ userUID: currentUser.uid, projectUID, kind: "comment-edit", scopeUID: comment.uid })
        : null;
    const editBaseRef = useRef({
        content: editDraft?.base_content !== undefined ? { content: editDraft.base_content } : content,
        revision: editDraft?.base_revision ?? revision,
        updatedAt: editDraft?.base_updated_at ? new Date(editDraft.base_updated_at) : updatedAt,
    });
    const valueRef = useRef<IEditorContent>(editDraft?.base_content !== undefined ? { content: editDraft.content } : content);
    const [recoveryDraft, setRecoveryDraft] = useState<IDraftPayload | null>(editDraft && editDraft.base_content === undefined ? editDraft : null);
    const [saveConflict, setSaveConflict] = useState(false);
    const pendingDraftRef = useRef<{ scope: IDraftScope; content: string; base: typeof editBaseRef.current } | null>(null);
    const hasServerConflict =
        isCurrentEditor &&
        (saveConflict ||
            editBaseRef.current.content.content !== content.content ||
            editBaseRef.current.revision !== revision ||
            editBaseRef.current.updatedAt.getTime() !== updatedAt.getTime());
    const currentUserUIDRef = useRef(currentUser.uid);
    currentUserUIDRef.current = currentUser.uid;
    const commentEditDraftScope = (): IDraftScope | null =>
        currentUserUIDRef.current ? { userUID: currentUserUIDRef.current, projectUID, kind: "comment-edit", scopeUID: comment.uid } : null;

    const flushDraftAutosave = () => {
        const pending = pendingDraftRef.current;
        pendingDraftRef.current = null;
        if (!pending) {
            return;
        }
        // Fence against account changes since the edit was captured.
        if (currentUserUIDRef.current !== pending.scope.userUID) {
            return;
        }
        getCardDraftStore().saveDraft(pending.scope, pending.content, {
            base_content: pending.base.content.content,
            base_revision: pending.base.revision,
            base_updated_at: pending.base.updatedAt.toISOString(),
        });
    };

    const cancelDraftAutosave = () => {
        pendingDraftRef.current = null;
        const scope = commentEditDraftScope();
        if (scope) {
            getCardDraftStore().clearDraft(scope);
        }
    };

    const setValue = (value: IEditorContent) => {
        valueRef.current = value;
        const scope = commentEditDraftScope();
        if (!scope || !isCurrentEditor) {
            return;
        }
        pendingDraftRef.current = { scope, content: value.content, base: editBaseRef.current };
        // Layout changes can remount the list before a debounce fires. Capture the
        // scoped draft immediately, as the new-comment composer already does.
        flushDraftAutosave();
    };
    const editorValue = isCurrentEditor ? valueRef.current : content;
    const openEditor = (value = content) => {
        setSaveConflict(false);
        editBaseRef.current = { content, revision, updatedAt };
        valueRef.current = value;
        setRecoveryDraft(null);
        getEditorStore().setCurrentEditor(editorName);
    };
    const beginEditing = () => {
        const scope = commentEditDraftScope();
        const draft = scope ? getCardDraftStore().getDraft(scope) : null;
        if (
            draft &&
            draft.content !== content.content &&
            (draft.base_content !== content.content ||
                draft.base_revision !== revision ||
                (draft.base_updated_at !== undefined && new Date(draft.base_updated_at).getTime() !== updatedAt.getTime()))
        ) {
            setRecoveryDraft(draft);
            return;
        }
        openEditor(draft ? { content: draft.content } : content);
    };
    useEffect(() => {
        if (recoveryDraft && isCurrentEditor) getEditorStore().setCurrentEditor(null);
    }, [recoveryDraft, isCurrentEditor]);

    useEffect(() => {
        const onPageHide = () => flushDraftAutosave();
        window.addEventListener("pagehide", onPageHide);
        return () => {
            window.removeEventListener("pagehide", onPageHide);
            flushDraftAutosave();
        };
        // Flush/cancel logic reads refs only; the effect must be mounted once.
    }, []);

    return (
        <ModelRegistry.ProjectCardComment.Provider
            model={comment}
            params={{
                author: commentAuthor,
                deletedComment,
                valueRef,
                editorName,
                beginEditing,
                editBaseRef,
                hasServerConflict,
                markSaveConflict: () => setSaveConflict(true),
                isCurrentEditor,
                editorRef,
                flushDraftAutosave,
                cancelDraftAutosave,
            }}
        >
            <Box data-card-comment-uid={comment.uid} display="grid" gap="2" className="grid-cols-[theme(spacing.8),minmax(0,1fr)]">
                <Box>
                    <BoardCommentUserAvatar projectUID={projectUID} cardUID={card.uid} />
                </Box>
                <Flex direction="col" gap="2" className="min-w-0 max-w-full">
                    <BoardCommentHeader />
                    {recoveryDraft && (
                        <div role="alert" className="alert alert-vertical sm:alert-horizontal">
                            <span>
                                {t("card.This draft was based on a different saved comment. Review the current comment before restoring it.")}
                            </span>
                            <Button onClick={() => openEditor({ content: recoveryDraft.content })}>{t("card.Restore draft")}</Button>
                            <Button
                                onClick={() => {
                                    cancelDraftAutosave();
                                    openEditor();
                                }}
                            >
                                {t("card.Discard draft")}
                            </Button>
                            <Button onClick={() => setRecoveryDraft(null)}>{t("common.Cancel")}</Button>
                        </div>
                    )}
                    {hasServerConflict && (
                        <div role="alert" className="alert alert-vertical sm:alert-horizontal">
                            <span>{t("card.This comment changed elsewhere. Your draft is preserved. Reload before saving.")}</span>
                            <Button
                                onClick={() => {
                                    flushDraftAutosave();
                                    if (saveConflict) window.location.reload();
                                    else getEditorStore().setCurrentEditor(null);
                                }}
                            >
                                {t(saveConflict ? "card.Reload saved comment" : "card.Show saved comment")}
                            </Button>
                        </div>
                    )}
                    {anchor && (
                        <button
                            type="button"
                            className={cn(
                                "max-w-full truncate rounded-md border-l-2 border-brand bg-brand/10 px-2 py-1 text-left text-xs",
                                "text-muted-foreground hover:bg-brand/15"
                            )}
                            title={isImageCommentAnchor(anchor) ? t("card.Image annotation") : anchor.exact}
                            onClick={() => {
                                if (isImageCommentAnchor(anchor)) {
                                    nativeImages?.jump(anchor, comment.uid);
                                    return;
                                }
                                const description = commentSurfaceRef.current?.querySelector<HTMLElement>("[data-card-description]");
                                if (!description) {
                                    return;
                                }
                                resolveCardCommentAnchorElement(description, anchor)?.scrollIntoView({
                                    behavior: "smooth",
                                    block: "center",
                                });
                            }}
                        >
                            {isImageCommentAnchor(anchor)
                                ? t(nativeImages?.isCurrent(anchor) ? "card.Image annotation" : "card.Image changed or missing")
                                : `“${anchor.exact}”`}
                        </button>
                    )}
                    <Flex
                        px="3"
                        py="1.5"
                        rounded="lg"
                        className={cn(
                            "min-w-0 rounded-ss-none bg-accent/70",
                            isCurrentEditor ? "w-full max-w-full overflow-hidden border bg-transparent p-0" : "w-fit max-w-full"
                        )}
                    >
                        <PlateEditor
                            key={`board-comment-editor-${comment.uid}-${isCurrentEditor ? "edit" : updatedAt.getTime()}`}
                            value={editorValue}
                            currentUser={currentUser}
                            mentionables={mentionables}
                            linkables={cards}
                            className={
                                isCurrentEditor ? "h-full max-h-[min(70vh,300px)] min-h-[min(70vh,300px)] min-w-0 overflow-y-auto px-4 py-3" : ""
                            }
                            focusOnReady={isCurrentEditor}
                            readOnly={!isCurrentEditor}
                            editorType={EEditorType.CardComment}
                            form={{
                                project_uid: projectUID,
                                card_uid: card.uid,
                                comment_uid: comment.uid,
                            }}
                            setValue={setValue}
                            editorRef={editorRef}
                        />
                    </Flex>
                    <BoardCommentFooter />
                </Flex>
            </Box>
        </ModelRegistry.ProjectCardComment.Provider>
    );
});

function BoardCommentUserAvatar({ projectUID, cardUID }: { projectUID: string; cardUID: string }): React.JSX.Element {
    const { params } = ModelRegistry.ProjectCardComment.useContext<IBoardCommentContextParams>();
    const { author } = params;

    return (
        <UserAvatar.Root avatarSize="sm" userOrBot={author}>
            <UserAvatarDefaultList
                userOrBot={author}
                scope={{
                    projectUID,
                    cardUID,
                }}
            />
        </UserAvatar.Root>
    );
}

function BoardCommentHeader(): React.JSX.Element {
    const [t] = useTranslation();
    const { model: comment, params } = ModelRegistry.ProjectCardComment.useContext<IBoardCommentContextParams>();
    const { author } = params;
    const commentedAt = comment.useField("updated_at");
    const isEdited = comment.useField("is_edited");

    return (
        <Flex gap="2" items="center">
            <span className="text-sm font-bold">
                <UserLikeComponent
                    userOrBot={author}
                    userComp={BoardCommentUserHeader}
                    botComp={BoardCommentBotHeader}
                    customNullReturn={<Skeleton className="h-4 w-24" />}
                />
            </span>
            <span className="text-xs text-accent-foreground/50">
                <DateDistance date={commentedAt} />
                {isEdited && ` (${t("card.edited")})`}
            </span>
        </Flex>
    );
}

function BoardCommentUserHeader({ user }: { user: User.TModel }): React.JSX.Element {
    const [t] = useTranslation();
    const type = user.useField("type");
    const firstname = user.useField("firstname");
    const lastname = user.useField("lastname");

    if (user.isDeletedUser(type)) {
        return <>{t("common.Unknown User")}</>;
    }

    return (
        <>
            {firstname} {lastname}
        </>
    );
}

function BoardCommentBotHeader({ bot }: { bot: BotModel.TModel }): React.JSX.Element {
    const name = bot.useField("name");

    return <>{name}</>;
}

export default BoardComment;
