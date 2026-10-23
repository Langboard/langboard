import { useBoardSplitPane } from "@/core/providers/BoardSplitProvider";
import Toast from "@/components/base/Toast";
import useCreateCard from "@/controllers/api/board/useCreateCard";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import { ProjectColumn } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import { useBoard } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { getCardDraftStore, type IDraftScope } from "@/core/stores/CardDraftStore";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { usePageSearchParams } from "@/core/hooks/usePageSearchParams";

export interface IBoardAddCardContext {
    isEditing: bool;
    setIsEditing: (isEditing: bool) => void;
    isValidating: bool;
    changeMode: (mode: "edit" | "view") => void;
    scrollToBottom: () => void;
    textareaRef: React.RefObject<HTMLTextAreaElement | null>;
    disableChangeModeAttr: string;
    canWrite: bool;
    saveTitleDraft: (content: string) => void;
}

interface IBoardAddCardProviderProps {
    column: ProjectColumn.TModel;
    viewportRef: React.RefObject<HTMLDivElement | null>;
    toLastPage: () => void;
    isDefaultCardColumn: boolean;
    children: React.ReactNode;
}

const initialContext = {
    isEditing: false,
    setIsEditing: () => {},
    isValidating: false,
    changeMode: () => {},
    scrollToBottom: () => {},
    textareaRef: { current: null! },
    disableChangeModeAttr: "data-disable-change-mode",
    canWrite: false,
    saveTitleDraft: () => {},
};

const BoardAddCardContext = createContext<IBoardAddCardContext>(initialContext);

export const BoardAddCardProvider = ({
    column,
    viewportRef,
    toLastPage,
    isDefaultCardColumn,
    children,
}: IBoardAddCardProviderProps): React.ReactNode => {
    const paneID = useBoardSplitPane();
    const { project, currentUser, hasRoleAction } = useBoard();
    const [t] = useTranslation();
    const [isValidating, setIsValidating] = useState(false);
    const [searchParams, setSearchParams] = usePageSearchParams();
    const newCardCommandHandled = useRef(false);
    const disableChangeModeAttr = "data-disable-change-mode";
    const canWrite = hasRoleAction(ProjectRole.EAction.CardWrite) && !column.is_archive;
    const { mutateAsync: createCardMutateAsync } = useCreateCard({ interceptToast: true });
    const editorName = `${column.uid}-add-card`;
    const titleDraftScope: IDraftScope = { userUID: currentUser.uid, projectUID: project.uid, kind: "card-new", scopeUID: column.uid };
    const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingDraft = useRef<{ scope: IDraftScope; content: string } | null>(null);
    const flushTitleDraft = useCallback(() => {
        if (draftTimer.current) clearTimeout(draftTimer.current);
        draftTimer.current = null;
        const pending = pendingDraft.current;
        pendingDraft.current = null;
        if (pending && useAuthStore.getState().currentUser?.uid === pending.scope.userUID) {
            getCardDraftStore().saveDraft(pending.scope, pending.content);
        }
    }, []);
    const saveTitleDraft = (content: string) => {
        if (useAuthStore.getState().currentUser?.uid !== currentUser.uid) return;
        pendingDraft.current = { scope: { ...titleDraftScope }, content };
        if (draftTimer.current) clearTimeout(draftTimer.current);
        draftTimer.current = setTimeout(flushTitleDraft, 600);
    };
    useEffect(() => {
        window.addEventListener("pagehide", flushTitleDraft);
        return () => {
            window.removeEventListener("pagehide", flushTitleDraft);
            flushTitleDraft();
        };
    }, [flushTitleDraft]);

    const scrollToCreatedCard = (cardUID: string, attemptsLeft = 40) => {
        if (document.getElementById(`board-card-${cardUID}`)) {
            scrollToBottom();
            return;
        }
        if (attemptsLeft > 0) {
            window.setTimeout(() => scrollToCreatedCard(cardUID, attemptsLeft - 1), 50);
        }
    };

    const { valueRef, isEditing, setIsEditing, changeMode } = useChangeEditMode({
        canEdit: () => hasRoleAction(ProjectRole.EAction.CardWrite) && !column.is_archive,
        valueType: "textarea",
        disableNewLine: true,
        editorName,
        independent: !!paneID,
        customStartEditing: () => {
            toLastPage();

            setTimeout(() => {
                if (valueRef.current && useAuthStore.getState().currentUser?.uid === currentUser.uid) {
                    const draft = getCardDraftStore().getDraft(titleDraftScope);
                    if (draft && !valueRef.current.value) valueRef.current.value = draft.content;
                    valueRef.current.focus();
                    valueRef.current.scrollIntoView({ block: "nearest", inline: "nearest" });
                    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
                        valueRef.current.animate(
                            [{ boxShadow: "0 0 0 3px hsl(var(--primary) / 0.65)" }, { boxShadow: "0 0 0 0 hsl(var(--primary) / 0)" }],
                            { duration: 700, easing: "ease-out" }
                        );
                    }
                }
                scrollToBottom();
            }, 0);
        },
        save: (value, endCallback) => {
            if (useAuthStore.getState().currentUser?.uid !== currentUser.uid) return;
            const submittedScope = { ...titleDraftScope };
            saveTitleDraft(value);
            flushTitleDraft();
            setIsValidating(true);

            const promise = createCardMutateAsync({
                project_uid: project.uid,
                project_column_uid: column.uid,
                title: value,
            });

            Toast.Add.promise(promise, {
                loading: t("common.Adding..."),
                error: (error) => {
                    const messageRef = { message: "" };
                    const { handle } = setupApiErrorHandler({}, messageRef);

                    handle(error);
                    return messageRef.message;
                },
                success: ({ uid }) => {
                    getCardDraftStore().clearDraft(submittedScope);
                    if (useAuthStore.getState().currentUser?.uid !== submittedScope.userUID) {
                        return t("successes.Card added successfully.");
                    }
                    // Card creation stays lightweight: insert into the board without opening the viewer.
                    // The user opens the viewer only through an explicit card or widget interaction.
                    toLastPage();
                    scrollToCreatedCard(uid);
                    setIsEditing(() => false);
                    endCallback();
                    return t("successes.Card added successfully.");
                },
                finally: () => {
                    setIsValidating(false);
                },
            });
        },
    });

    useEffect(() => {
        flushTitleDraft();
        if (valueRef.current) {
            valueRef.current.value =
                useAuthStore.getState().currentUser?.uid === currentUser.uid
                    ? (getCardDraftStore().getDraft({ userUID: currentUser.uid, projectUID: project.uid, kind: "card-new", scopeUID: column.uid })
                          ?.content ?? "")
                    : "";
        }
    }, [currentUser.uid, project.uid, column.uid, flushTitleDraft, valueRef]);

    useEffect(() => {
        if (searchParams.get("new-card") !== "1") {
            newCardCommandHandled.current = false;
            return;
        }
        if (!isDefaultCardColumn || !canWrite || newCardCommandHandled.current) return;
        newCardCommandHandled.current = true;
        changeMode("edit");
        setSearchParams(
            (params) => {
                params.delete("new-card");
                return params;
            },
            { replace: true }
        );
    }, [isDefaultCardColumn, canWrite, searchParams, changeMode, setSearchParams]);

    useEffect(() => {
        if (!isEditing) {
            return;
        }

        const pointerDownEvent = (e: PointerEvent) => {
            const path = e.composedPath();
            const targetPane = path.find((node) => node instanceof HTMLElement && node.hasAttribute("data-board-pane"));
            if (paneID && targetPane instanceof HTMLElement && targetPane.getAttribute("data-board-pane") !== paneID) return;
            const protectedTarget = path.some(
                (node) => node instanceof HTMLElement && (node.hasAttribute(disableChangeModeAttr) || node.hasAttribute("data-board-compose"))
            );
            if (!protectedTarget) changeMode("view");
        };

        window.addEventListener("pointerdown", pointerDownEvent);
        return () => window.removeEventListener("pointerdown", pointerDownEvent);
    }, [isEditing, changeMode, disableChangeModeAttr, paneID]);

    const scrollToBottom = () => {
        const viewport = viewportRef.current;
        if (!viewport) {
            return;
        }

        viewport.scrollTo({ top: viewport.scrollHeight });
    };

    return (
        <BoardAddCardContext.Provider
            value={{
                isEditing,
                setIsEditing,
                isValidating,
                changeMode,
                scrollToBottom,
                textareaRef: valueRef,
                disableChangeModeAttr,
                canWrite,
                saveTitleDraft,
            }}
        >
            {children}
        </BoardAddCardContext.Provider>
    );
};

export const useBoardAddCard = () => {
    const context = useContext(BoardAddCardContext);
    if (!context) {
        throw new Error("useBoardAddCard must be used within a BoardAddCardProvider");
    }
    return context;
};
