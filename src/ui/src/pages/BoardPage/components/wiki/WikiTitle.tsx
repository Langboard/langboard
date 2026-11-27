import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Collaborative from "@/components/Collaborative";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useChangeWikiDetails from "@/controllers/api/wiki/useChangeWikiDetails";
import useResizeEvent from "@/core/hooks/useResizeEvent";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectWiki } from "@/core/models";
import { useBoardWiki } from "@/core/providers/BoardWikiProvider";
import { ROUTES } from "@/core/routing/constants";
import { cn, measureTextAreaHeight } from "@/core/utils/ComponentUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { KeyboardEvent, PointerEvent, memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useBoardWikiUnsavedActions } from "@/pages/BoardPage/components/wiki/BoardWikiUnsavedProvider";

export interface IWikiTitleProps {
    wiki: ProjectWiki.TModel;
}

const WikiTitle = memo(({ wiki }: IWikiTitleProps) => {
    const navigate = usePageNavigateRef();
    const { project, canEditWiki, isWikiEditing } = useBoardWiki();
    const [t] = useTranslation();
    const { markSectionDirty, resetSection, registerSectionSaveHandler, registerSectionCancelHandler } = useBoardWikiUnsavedActions();
    const { mutateAsync: changeWikiDetailsMutateAsync } = useChangeWikiDetails("title", { interceptToast: true });
    const title = wiki.useField("title");
    const forbidden = wiki.useField("forbidden");
    const canStartEditing = isWikiEditing && canEditWiki(wiki.uid) && !forbidden;
    const titleSpanRef = useRef<HTMLSpanElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [draftTitle, setDraftTitle] = useState(title);
    const [height, setHeight] = useState(0);
    const [isOpened, setIsOpened] = useState(false);
    const [isTitleWrapping, setIsTitleWrapping] = useState(false);
    const [titleMaxHeight, setTitleMaxHeight] = useState(32);
    const [showCollapse, setShowCollapse] = useState(false);
    const [isEditing, setIsEditing] = useState(false);

    const updateShowCollapse = useCallback(() => {
        const titleSpan = titleSpanRef.current;
        if (!titleSpan) {
            setShowCollapse(false);
            return;
        }

        const isOverflowed = isTitleWrapping ? showCollapse : titleSpan.scrollWidth > titleSpan.clientWidth;
        setShowCollapse(isOverflowed);
        setTitleMaxHeight(isTitleWrapping ? titleSpan.scrollHeight : 32);
    }, [isTitleWrapping, showCollapse]);

    const openTitle = useCallback(() => {
        const titleSpan = titleSpanRef.current;
        setIsOpened(true);
        setIsTitleWrapping(true);
        setTitleMaxHeight(titleSpan?.clientHeight || 32);

        requestAnimationFrame(() => {
            const nextTitleSpan = titleSpanRef.current;
            if (!nextTitleSpan) {
                return;
            }

            setTitleMaxHeight(nextTitleSpan.scrollHeight);
        });
    }, []);

    const closeTitle = useCallback(() => {
        const titleSpan = titleSpanRef.current;
        setIsOpened(false);
        setTitleMaxHeight(titleSpan?.scrollHeight || 32);

        requestAnimationFrame(() => {
            setTitleMaxHeight(32);
        });

        window.setTimeout(() => {
            setIsTitleWrapping(false);
        }, 200);
    }, []);

    const handleToggleOpened = useCallback(() => {
        if (isOpened) {
            closeTitle();
            return;
        }

        openTitle();
    }, [closeTitle, isOpened, openTitle]);

    const syncHeight = useCallback(() => {
        if (!textareaRef.current) {
            return;
        }

        setHeight(measureTextAreaHeight(textareaRef.current));
    }, []);

    const saveTitle = useCallback(async () => {
        const nextTitle = draftTitle.trim();
        const originalTitle = title.trim();

        if (!nextTitle || nextTitle === originalTitle) {
            setDraftTitle(title);
            resetSection("title");
            return;
        }

        const promise = changeWikiDetailsMutateAsync({
            project_uid: project.uid,
            wiki_uid: wiki.uid,
            title: nextTitle,
        });

        await Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler(
                    {
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.BOARD.WIKI(project.uid)),
                        },
                    },
                    messageRef
                );

                handle(error);
                return messageRef.message;
            },
            success: () => t("successes.Title changed successfully."),
        });

        resetSection("title");
    }, [changeWikiDetailsMutateAsync, draftTitle, project, resetSection, title, wiki]);

    const cancelTitleEdit = useCallback(() => {
        setDraftTitle(title);
        resetSection("title");
    }, [resetSection, title]);

    const handleStartEditing = useCallback(
        (e: PointerEvent<HTMLSpanElement>) => {
            if (!canStartEditing) {
                return;
            }

            e.preventDefault();
            e.stopPropagation();

            requestAnimationFrame(() => {
                setIsEditing(true);
            });
        },
        [canStartEditing]
    );

    const handleTitleValueChange = useCallback(
        (nextTitle: string) => {
            setDraftTitle(nextTitle);
            markSectionDirty("title", nextTitle.trim() !== title.trim());
            window.requestAnimationFrame(syncHeight);
        },
        [markSectionDirty, syncHeight, title]
    );

    const handleTitleKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key !== "Enter") {
            return;
        }

        e.preventDefault();
        e.stopPropagation();
    }, []);

    useLayoutEffect(() => {
        if (!isEditing) {
            return;
        }

        syncHeight();
        textareaRef.current?.focus();
    }, [isEditing, syncHeight]);

    useEffect(() => {
        if (isEditing) {
            return;
        }

        setDraftTitle(title);
    }, [isEditing, title]);

    useLayoutEffect(() => {
        requestAnimationFrame(updateShowCollapse);
    }, [title, updateShowCollapse]);

    useResizeEvent({ doneCallback: updateShowCollapse }, [updateShowCollapse]);

    useEffect(() => {
        if (!isWikiEditing) {
            setIsEditing(false);
            setDraftTitle(title);
            resetSection("title");
        }
    }, [isWikiEditing, resetSection, title]);

    useEffect(() => registerSectionSaveHandler("title", saveTitle), [registerSectionSaveHandler, saveTitle]);
    useEffect(() => registerSectionCancelHandler("title", cancelTitleEdit), [cancelTitleEdit, registerSectionCancelHandler]);

    useEffect(() => {
        if (isWikiEditing || !isEditing) {
            return;
        }

        setIsEditing(false);
    }, [isEditing, isWikiEditing]);

    return (
        <Box p="2">
            {!isEditing ? (
                <h1 className="min-h-8 border-b border-border text-xl md:text-2xl">
                    <Flex className="min-w-0">
                        <span
                            className={cn(
                                "block min-w-0 overflow-hidden transition-[max-height] duration-200 ease-in-out",
                                isTitleWrapping ? "whitespace-normal break-all" : "truncate",
                                canStartEditing ? "cursor-text rounded-sm hover:bg-accent/40" : "cursor-default"
                            )}
                            style={{ maxHeight: titleMaxHeight }}
                            ref={titleSpanRef}
                            onPointerDown={handleStartEditing}
                        >
                            {title}
                        </span>
                        <Flex items="start" gap="1" ml="1" className="shrink-0">
                            <Button
                                variant="ghost"
                                size="icon-sm"
                                onClick={handleToggleOpened}
                                title={t(`card.${isOpened ? "Hide" : "Show"} title`)}
                                className={showCollapse ? "" : "hidden"}
                            >
                                <IconComponent icon="chevron-down" size="5" className={cn("transition-all", isOpened ? "rotate-180" : "")} />
                            </Button>
                        </Flex>
                    </Flex>
                </h1>
            ) : (
                <Collaborative.Textarea
                    ref={textareaRef}
                    collaborationType={EEditorCollaborationType.Wiki}
                    uid={wiki.uid}
                    section="title"
                    field="title"
                    className={cn(
                        "min-h-8 break-all rounded-none border-x-0 border-t-0 p-0 pb-px text-xl md:text-2xl",
                        "scrollbar-hide focus-visible:border-b-primary focus-visible:ring-0"
                    )}
                    resize="none"
                    style={{ height }}
                    defaultValue={title}
                    onValueChange={handleTitleValueChange}
                    onKeyDown={handleTitleKeyDown}
                />
            )}
        </Box>
    );
});

export default WikiTitle;
