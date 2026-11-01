import { isImageCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
import { useNativeImageComments } from "@/components/ImagePreviewDialog/NativeImageCommentContext";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Drawer from "@/components/base/Drawer";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import Skeleton from "@/components/base/Skeleton";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import UserAvatar from "@/components/UserAvatar";
import { useTranslation } from "react-i18next";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PlateEditor } from "@/components/Editor/plate-editor";
import { isEmptyEditorContent, sanitizeEditorContent, sanitizeEditorValue } from "@/components/Editor/utils";
import { IEditorContent } from "@/core/models/Base";
import { useBoardCard, useBoardCardPanel } from "@/core/providers/BoardCardProvider";
import useAddCardComment from "@/controllers/api/card/comment/useAddCardComment";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useToggleEditingByClickOutside from "@/core/hooks/useToggleEditingByClickOutside";
import { isModel, TUserLikeModel } from "@/core/models/ModelRegistry";
import { BotModel, ProjectCard } from "@/core/models";
import { getEditorStore, useIsCurrentEditor } from "@/core/stores/EditorStore";
import { getCardCommentDraftStore } from "@/core/stores/CardCommentDraftStore";
import { getCardDraftStore } from "@/core/stores/CardDraftStore";
import { TEditor } from "@/components/Editor/editor-kit";
import { EEditorType } from "@langboard/core/constants";
import { getMentionOnSelectItem } from "@platejs/mention";
import type { ICardCommentAnchor } from "@/core/models/types/card-comment-anchor.type";

export function SkeletonBoardCommentForm() {
    return (
        <Box
            position="sticky"
            bottom={{ initial: "0", sm: "-2" }}
            className="-ml-[calc(theme(spacing.4))] w-[calc(100%_+_theme(spacing.8))] bg-background"
        >
            <Flex items="center" gap="4" p="2" className="rounded-b-lg border-t">
                <Skeleton size="8" rounded="full" className="overflow-hidden" />
                <Box w="full" cursor="text" py="1">
                    <Skeleton h="6" className="w-1/3" />
                </Box>
            </Flex>
        </Box>
    );
}
export interface IBoardCommentFormProps {
    variant?: "mobile" | "panel";
}

const insertMention = getMentionOnSelectItem();

const BoardCommentForm = memo(({ variant = "mobile" }: IBoardCommentFormProps): React.JSX.Element | null => {
    const { projectUID, card, currentUser, replyRef, anchoredCommentRef } = useBoardCard();
    const { isCommentPanelOpen, setIsCommentPanelOpen, commentLayoutMode } = useBoardCardPanel();
    const [t] = useTranslation();
    const isPanelLayout = commentLayoutMode === "panel";
    const isVisible = isCommentPanelOpen;
    const projectMembers = card.useForeignFieldArray("project_members");
    const visibility = card.useField("visibility");
    const bots = BotModel.Model.useModels(() => true);
    const mentionables = useMemo(() => (visibility === "PRIVATE" ? [] : [...projectMembers, ...bots]), [visibility, projectMembers, bots]);
    const cards = ProjectCard.Model.useModels(
        (model) => model.uid !== card.uid && model.project_uid === projectUID,
        [currentUser.uid, projectUID, card]
    );
    const valueRef = useRef<IEditorContent>({ content: getCardCommentDraftStore().getDraft(currentUser.uid, projectUID, card.uid) });
    const setValue = useCallback(
        (value: IEditorContent) => {
            // An idempotency key belongs to one image-comment payload, including after remount.
            if (
                isImageCommentAnchor(pendingAnchorRef.current) &&
                sanitizeEditorContent(valueRef.current.content) !== sanitizeEditorContent(value.content)
            ) {
                requestUIDRef.current = crypto.randomUUID();
            }
            valueRef.current = value;
            getCardCommentDraftStore().saveSnapshot(currentUser.uid, projectUID, card.uid, {
                content: sanitizeEditorContent(value.content),
                anchor: pendingAnchorRef.current,
                request_uid: requestUIDRef.current,
            });
        },
        [currentUser.uid, projectUID, card.uid]
    );
    const drawerRef = useRef<HTMLDivElement>(null);
    const closeDrawerTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const editorName = `${card.uid}-comment-form`;
    const isCurrentEditor = useIsCurrentEditor(editorName);
    const saveSessionRef = useRef(0);
    useEffect(() => {
        saveSessionRef.current++;
        return () => {
            saveSessionRef.current++;
        };
    }, [currentUser.uid, projectUID, card.uid, isCurrentEditor]);
    useEffect(
        () => () => {
            if (getEditorStore().currentEditor === editorName) getEditorStore().setCurrentEditor(null);
        },
        [editorName]
    );
    const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState(false);
    const [isPanelEditorOpen, setIsPanelEditorOpen] = useState(false);
    const [pendingReplyMention, setPendingReplyMention] = useState<{ uid: string; username: string } | null>(null);
    const nativeImages = useNativeImageComments();
    const draftSnapshot = () => getCardCommentDraftStore().getSnapshot(currentUser.uid, projectUID, card.uid);
    const [pendingAnchor, setPendingAnchor] = useState<ICardCommentAnchor | null>(() => draftSnapshot().anchor ?? null);
    const requestUIDRef = useRef<string | undefined>(draftSnapshot().request_uid);
    const pendingAnchorRef = useRef(pendingAnchor);
    pendingAnchorRef.current = pendingAnchor;
    const [isValidating, setIsValidating] = useState(false);
    const { mutate: addCommentMutate } = useAddCardComment();
    const isClickedRef = useRef(false);
    const isReplyOwner = variant === "panel" ? isPanelLayout : !isPanelLayout;
    const { stopEditing } = useToggleEditingByClickOutside("[data-card-comment-form]", (mode) => {
        if (mode === "view") {
            getEditorStore().setCurrentEditor(null);
        }
    });

    const saveDraftToStorage = useCallback(
        (content: string) => {
            const draft = {
                content: sanitizeEditorContent(content),
                anchor: pendingAnchorRef.current,
                request_uid: requestUIDRef.current,
            };
            const stored = getCardCommentDraftStore().getSnapshot(currentUser.uid, projectUID, card.uid);
            // Blur flushes must preserve the submitted revision; typing and reopening save a new one.
            if (JSON.stringify(stored) !== JSON.stringify(draft)) {
                getCardCommentDraftStore().saveSnapshot(currentUser.uid, projectUID, card.uid, draft);
            }
        },
        [currentUser.uid, projectUID, card]
    );
    const readDraftFromStorage = useCallback((): string => {
        return getCardCommentDraftStore().getDraft(currentUser.uid, projectUID, card.uid);
    }, [currentUser.uid, projectUID, card]);
    const clearDraftFromStorage = useCallback(() => {
        getCardCommentDraftStore().clearDraft(currentUser.uid, projectUID, card.uid);
    }, [currentUser.uid, projectUID, card]);
    const openEditor = useCallback(() => {
        if (getEditorStore().currentEditor !== editorName) setValue({ content: readDraftFromStorage() });
        getEditorStore().setCurrentEditor(editorName);
    }, [editorName, readDraftFromStorage, setValue]);
    const openMobileDrawer = useCallback(() => {
        if (closeDrawerTimeoutRef.current) {
            clearTimeout(closeDrawerTimeoutRef.current);
            closeDrawerTimeoutRef.current = null;
        }
        if (drawerRef.current) {
            drawerRef.current.style.display = "";
        }
        setIsMobileDrawerOpen(true);
        openEditor();
    }, [openEditor]);
    useEffect(() => {
        if (!isCurrentEditor || !isVisible) return;
        // A single composer changes placement without losing its draft or reply context.
        setIsPanelEditorOpen(variant === "panel");
        setIsMobileDrawerOpen(variant === "mobile");
    }, [isCurrentEditor, isVisible, variant]);

    const clearEditor = useCallback(() => {
        setValue({ content: "" });
        setPendingReplyMention(null);
        setPendingAnchor(null);
        pendingAnchorRef.current = null;
        requestUIDRef.current = undefined;
        clearDraftFromStorage();
    }, [clearDraftFromStorage, setValue]);

    const closePanelEditor = useCallback(() => {
        clearEditor();
        setIsPanelEditorOpen(false);
        getEditorStore().setCurrentEditor(null);
    }, [clearEditor]);
    const handleEditorReady = useCallback(
        (editor: TEditor) => {
            if (!pendingReplyMention) {
                return;
            }

            editor.tf.focus({ edge: "endEditor", retries: 3 });
            insertMention(editor, {
                key: pendingReplyMention.uid,
                text: pendingReplyMention.username,
            });
            editor.tf.insertText(" ");
            setPendingReplyMention(null);
        },
        [pendingReplyMention]
    );

    const closeMobileDrawer = useCallback(() => {
        if (closeDrawerTimeoutRef.current) {
            clearTimeout(closeDrawerTimeoutRef.current);
        }
        setIsMobileDrawerOpen(false);
        drawerRef.current?.setAttribute("data-state", "closed");
        closeDrawerTimeoutRef.current = setTimeout(() => {
            closeDrawerTimeoutRef.current = null;
            if (drawerRef.current) {
                drawerRef.current.style.display = "none";
            }
            if (getEditorStore().currentEditor === editorName) {
                getEditorStore().setCurrentEditor(null);
            }
        }, 450);
    }, [editorName]);
    useEffect(
        () => () => {
            if (closeDrawerTimeoutRef.current) {
                clearTimeout(closeDrawerTimeoutRef.current);
            }
        },
        []
    );
    const cancelMobileEditor = useCallback(() => {
        clearEditor();
        closeMobileDrawer();
    }, [clearEditor, closeMobileDrawer]);

    const onDrawerHandlePointerStart = useCallback(
        (type: "mouse" | "touch") => {
            if (isValidating) {
                return;
            }

            isClickedRef.current = false;
            const upEvent = type === "mouse" ? "mouseup" : "touchend";

            const checkIsClick = () => {
                isClickedRef.current = true;
                window.removeEventListener(upEvent, checkIsClick);
            };

            window.addEventListener(upEvent, checkIsClick);

            setTimeout(() => {
                if (isClickedRef.current) {
                    if (getEditorStore().currentEditor === editorName) {
                        getEditorStore().setCurrentEditor(null);
                    }
                    return;
                }

                window.removeEventListener(upEvent, checkIsClick);
            }, 250);
        },
        [editorName, isValidating]
    );

    useEffect(() => {
        if (!isReplyOwner) {
            return;
        }

        const handleReply = (target: TUserLikeModel) => {
            if (isValidating) {
                return;
            }

            setIsCommentPanelOpen(true);
            if (variant === "panel") {
                setIsPanelEditorOpen(true);
            } else {
                openMobileDrawer();
            }

            let username;
            if (isModel(target, "User")) {
                if (!target.isValidUser()) {
                    return;
                }

                username = target.username;
            } else if (isModel(target, "BotModel")) {
                username = target.bot_uname;
            } else {
                return;
            }

            setPendingReplyMention({ uid: target.uid, username });
            if (variant === "panel" && !isCurrentEditor) {
                openEditor();
            }
        };

        replyRef.current = handleReply;

        return () => {
            if (replyRef.current === handleReply) {
                replyRef.current = () => {};
            }
        };
    }, [isCurrentEditor, isReplyOwner, isValidating, openEditor, openMobileDrawer, setIsCommentPanelOpen, variant]);

    useEffect(() => {
        if (!isReplyOwner) {
            return;
        }

        const handleAnchoredComment = (anchor: ICardCommentAnchor) => {
            if (isValidating) {
                return;
            }
            setPendingAnchor(anchor);
            pendingAnchorRef.current = anchor;
            requestUIDRef.current = isImageCommentAnchor(anchor) ? crypto.randomUUID() : undefined;
            getCardCommentDraftStore().saveSnapshot(currentUser.uid, projectUID, card.uid, {
                content: valueRef.current.content,
                anchor,
                request_uid: requestUIDRef.current,
            });
            setIsCommentPanelOpen(true);
            if (variant === "panel") {
                setIsPanelEditorOpen(true);
            } else {
                setIsMobileDrawerOpen(true);
            }
            if (!isCurrentEditor) {
                openEditor();
            }
        };

        anchoredCommentRef.current = handleAnchoredComment;
        return () => {
            if (anchoredCommentRef.current === handleAnchoredComment) {
                anchoredCommentRef.current = () => {};
            }
        };
    }, [anchoredCommentRef, isCurrentEditor, isReplyOwner, isValidating, openEditor, setIsCommentPanelOpen, variant]);

    useEffect(() => {
        if (!isReplyOwner) return;
        const draft = getCardCommentDraftStore().getSnapshot(currentUser.uid, projectUID, card.uid);
        pendingAnchorRef.current = draft.anchor ?? null;
        requestUIDRef.current = draft.request_uid;
        setPendingAnchor(draft.anchor ?? null);
        valueRef.current = { content: draft.content };
    }, [isReplyOwner, currentUser.uid, projectUID, card.uid]);

    useEffect(() => {
        if (!isCurrentEditor && isReplyOwner) {
            saveDraftToStorage(valueRef.current.content);
        }
    }, [isCurrentEditor, isReplyOwner, saveDraftToStorage]);

    useEffect(() => {
        if (!isVisible && isCurrentEditor) {
            getEditorStore().setCurrentEditor(null);
        }
    }, [isCurrentEditor, isVisible]);

    useEffect(() => {
        if (variant === "panel" && !isVisible) {
            setIsPanelEditorOpen(false);
        }
    }, [isVisible, variant]);

    const changeOpenState = (opened: bool) => {
        if (isValidating) {
            return;
        }

        if (!opened) {
            closeMobileDrawer();
            return;
        }

        openMobileDrawer();
    };

    const saveComment = () => {
        if (isValidating) {
            return;
        }

        const submittedValue = valueRef.current;
        const saveSession = saveSessionRef.current;
        const submittedRequestUID = requestUIDRef.current;
        const content = sanitizeEditorValue(submittedValue);

        if (isEmptyEditorContent(content.content)) {
            Toast.Add.error(t("card.errors.Comment content cannot be empty."));
            return;
        }

        if (isImageCommentAnchor(pendingAnchor) && !nativeImages?.isCurrent(pendingAnchor)) {
            Toast.Add.error(t("card.Image changed or missing"));
            return;
        }
        saveDraftToStorage(content.content);
        const submittedScope = { userUID: currentUser.uid, projectUID, kind: "comment-new" as const, scopeUID: card.uid };
        const submittedDraft = getCardDraftStore().getDraft(submittedScope);
        setIsValidating(true);
        addCommentMutate(
            {
                project_uid: projectUID,
                card_uid: card.uid,
                content,
                anchor: pendingAnchor,
                request_uid: isImageCommentAnchor(pendingAnchor) ? requestUIDRef.current : undefined,
            },
            {
                onSuccess: () => {
                    // Storage acknowledgement survives focus transfer, but only for the exact submitted revision.
                    const isSubmittedDraft = submittedDraft !== null && getCardDraftStore().getDraft(submittedScope) === submittedDraft;
                    if (isSubmittedDraft) clearDraftFromStorage();
                    // UI cleanup still belongs only to the submitted editor session.
                    if (saveSessionRef.current !== saveSession) {
                        return;
                    }
                    if (valueRef.current !== submittedValue) {
                        // The completed request must not deduplicate the newer image comment.
                        if (requestUIDRef.current === submittedRequestUID) {
                            requestUIDRef.current = isImageCommentAnchor(pendingAnchorRef.current) ? crypto.randomUUID() : undefined;
                            saveDraftToStorage(valueRef.current.content);
                        }
                        return;
                    }
                    if (!isSubmittedDraft) return;
                    setValue({ content: "" });
                    setPendingAnchor(null);
                    pendingAnchorRef.current = null;
                    requestUIDRef.current = undefined;
                    clearDraftFromStorage();
                    getEditorStore().setCurrentEditor(null);

                    if (variant === "mobile") {
                        setTimeout(() => {
                            closeMobileDrawer();
                        }, 0);
                    }

                    if (variant === "panel") {
                        setIsPanelEditorOpen(false);
                    }
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({});

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    const clickOutside = (e: React.MouseEvent | CustomEvent) => {
        if (!isCurrentEditor) {
            return;
        }

        stopEditing(e);
    };

    if (!isVisible) {
        return null;
    }

    if (variant === "panel") {
        const isPanelComposerVisible = isPanelLayout && (isPanelEditorOpen || isCurrentEditor);

        return (
            <Form.Root className="w-full">
                <Box data-card-comment-form className="w-full">
                    {!isPanelComposerVisible ? (
                        <Flex
                            items="center"
                            gap="3"
                            rounded="lg"
                            role="button"
                            tabIndex={0}
                            border
                            px="3"
                            py="2.5"
                            className="cursor-text bg-background text-sm text-muted-foreground transition-colors hover:bg-accent/40"
                            onClick={() => {
                                setIsPanelEditorOpen(true);
                                openEditor();
                            }}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                    e.preventDefault();
                                    setIsPanelEditorOpen(true);
                                    openEditor();
                                }
                            }}
                        >
                            <UserAvatar.Root userOrBot={currentUser} avatarSize="sm" />
                            <Box>
                                {t("card.Add a comment as {firstname} {lastname}", {
                                    firstname: currentUser.firstname,
                                    lastname: currentUser.lastname,
                                })}
                            </Box>
                        </Flex>
                    ) : (
                        <Box rounded="lg" border className="overflow-hidden bg-background" data-card-comment-form>
                            {pendingAnchor && (
                                <Box px="4" py="2" className="border-b bg-brand/10 text-xs text-muted-foreground">
                                    {isImageCommentAnchor(pendingAnchor) ? t("card.Image annotation") : `“${pendingAnchor.exact}”`}
                                </Box>
                            )}
                            <PlateEditor
                                value={valueRef.current}
                                currentUser={currentUser}
                                mentionables={mentionables}
                                linkables={cards}
                                className="max-h-[240px] min-h-[140px] overflow-y-auto px-4 py-3"
                                editorType={EEditorType.CardNewComment}
                                focusOnReady
                                form={{
                                    project_uid: projectUID,
                                    card_uid: card.uid,
                                }}
                                setValue={setValue}
                                onEditorReady={handleEditorReady}
                            />
                            <Flex items="center" gap="2" justify="end" p="2" className="border-t">
                                <Button variant="secondary" onClick={closePanelEditor} disabled={isValidating}>
                                    {t("common.Cancel")}
                                </Button>
                                <SubmitButton type="button" onClick={saveComment} isValidating={isValidating}>
                                    {t("common.Save")}
                                </SubmitButton>
                            </Flex>
                        </Box>
                    )}
                </Box>
            </Form.Root>
        );
    }

    return (
        <Form.Root className="w-full">
            <Drawer.Root
                modal={false}
                handleOnly
                repositionInputs={false}
                open={isMobileDrawerOpen && isCommentPanelOpen && !isPanelLayout}
                onOpenChange={changeOpenState}
            >
                <Flex
                    items="center"
                    gap="4"
                    p="2"
                    role="button"
                    tabIndex={0}
                    className="cursor-text rounded-lg border bg-background shadow-sm"
                    onClick={openMobileDrawer}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            openMobileDrawer();
                        }
                    }}
                >
                    <UserAvatar.Root userOrBot={currentUser} avatarSize="sm" />
                    <Box w="full" cursor="text" py="1">
                        {t("card.Add a comment as {firstname} {lastname}", { firstname: currentUser.firstname, lastname: currentUser.lastname })}
                    </Box>
                </Flex>
                <Drawer.Content
                    withGrabber={false}
                    className="rounded-t-none border-none bg-transparent"
                    aria-describedby=""
                    focusGuards={false}
                    onPointerDownOutside={clickOutside}
                    onClick={clickOutside}
                    ref={drawerRef}
                >
                    <Drawer.Title hidden />
                    <Flex
                        direction="col"
                        position="relative"
                        mx="auto"
                        w="full"
                        pt="4"
                        border
                        className="max-w-[100vw] rounded-t-[10px] bg-background sm:max-w-screen-sm lg:max-w-screen-md"
                        data-card-comment-form
                    >
                        <Drawer.Handle
                            className="flex h-2 !w-full cursor-grab justify-center !bg-transparent py-3 text-center"
                            onMouseDown={() => onDrawerHandlePointerStart("mouse")}
                            onTouchStart={() => onDrawerHandlePointerStart("touch")}
                        >
                            <Box display="inline-block" h="2" rounded="full" className="w-[100px] bg-muted" />
                        </Drawer.Handle>
                        <Box position="relative" w="full" className="border-b">
                            {pendingAnchor && (
                                <Box px="6" py="2" className="border-b bg-brand/10 text-xs text-muted-foreground">
                                    {isImageCommentAnchor(pendingAnchor) ? t("card.Image annotation") : `“${pendingAnchor.exact}”`}
                                </Box>
                            )}
                            <PlateEditor
                                value={valueRef.current}
                                currentUser={currentUser}
                                mentionables={mentionables}
                                linkables={cards}
                                className="h-full max-h-[min(50vh,200px)] min-h-[min(50vh,200px)] overflow-y-auto px-6 py-3"
                                editorType={EEditorType.CardNewComment}
                                focusOnReady
                                form={{
                                    project_uid: projectUID,
                                    card_uid: card.uid,
                                }}
                                setValue={setValue}
                                onEditorReady={handleEditorReady}
                            />
                        </Box>
                        <Flex items="center" gap="2" justify="start" p="1">
                            <Button variant="secondary" onClick={cancelMobileEditor} disabled={isValidating}>
                                {t("common.Cancel")}
                            </Button>
                            <SubmitButton type="button" onClick={saveComment} isValidating={isValidating}>
                                {t("common.Save")}
                            </SubmitButton>
                        </Flex>
                    </Flex>
                </Drawer.Content>
            </Drawer.Root>
        </Form.Root>
    );
});

export default BoardCommentForm;
