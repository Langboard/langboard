import { useBoardSplitPane } from "@/core/providers/BoardSplitProvider";
import Toast from "@/components/base/Toast";
import useCreateCard from "@/controllers/api/board/useCreateCard";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import { ProjectColumn } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import { useBoard } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { buildDraftScopeKey, getCardDraftStore, type IDraftScope } from "@/core/stores/CardDraftStore";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
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
    description: string;
    saveDescriptionDraft: (content: string) => void;
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
    description: "",
    saveDescriptionDraft: () => {},
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
    const authUID = useAuthStore((state) => state.currentUser?.uid);
    const accountVersion = useAuthStore((state) => state.getIdentityVersion());
    const [searchParams, setSearchParams] = usePageSearchParams();
    const newCardCommandHandled = useRef(false);
    const disableChangeModeAttr = "data-disable-change-mode";
    const canWrite = hasRoleAction(ProjectRole.EAction.CardWrite) && !column.is_archive;
    const { mutateAsync: createCardMutateAsync } = useCreateCard({ interceptToast: true });
    const editorName = `${column.uid}-add-card`;
    const titleDraftScope: IDraftScope = { userUID: currentUser.uid, projectUID: project.uid, kind: "card-new", scopeUID: column.uid };
    const scopeKey = buildDraftScopeKey(titleDraftScope);
    const [description, setDescription] = useState("");
    const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingDraft = useRef<{ scope: IDraftScope; content: string; description: string; request_uid: string } | null>(null);
    const composer = useRef({ scopeKey, session: crypto.randomUUID(), title: "", description: "", requestUID: "", submitting: false });
    const mounted = useRef(true);
    const flushDraft = useCallback(() => {
        if (draftTimer.current) clearTimeout(draftTimer.current);
        draftTimer.current = null;
        const pending = pendingDraft.current;
        pendingDraft.current = null;
        if (pending) {
            // The captured scope owns this input, including during account/navigation cleanup.
            getCardDraftStore().saveDraft(pending.scope, pending.content, {
                description: pending.description,
                ...(pending.content || pending.description ? { request_uid: pending.request_uid } : {}),
            });
        }
    }, []);
    const saveFields = (title: string, body: string) => {
        if (
            useAuthStore.getState().currentUser?.uid !== currentUser.uid ||
            useAuthStore.getState().getIdentityVersion() !== accountVersion ||
            composer.current.scopeKey !== scopeKey
        )
            return false;
        const requestUID = crypto.randomUUID();
        Object.assign(composer.current, { title, description: body, requestUID });
        pendingDraft.current = { scope: { ...titleDraftScope }, content: title, description: body, request_uid: requestUID };
        if (draftTimer.current) clearTimeout(draftTimer.current);
        draftTimer.current = setTimeout(flushDraft, 600);
        return true;
    };
    const saveTitleDraft = (content: string) => saveFields(content, composer.current.description);
    const saveDescriptionDraft = (content: string) => {
        if (saveFields(valueRef.current?.value ?? composer.current.title, content)) setDescription(content);
    };
    useEffect(() => {
        mounted.current = true;
        window.addEventListener("pagehide", flushDraft);
        return () => {
            mounted.current = false;
            window.removeEventListener("pagehide", flushDraft);
            flushDraft();
        };
    }, [flushDraft]);

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
            flushDraft();
            composer.current = { ...composer.current, session: crypto.randomUUID(), submitting: false };
            setIsValidating(false);
            toLastPage();
            const session = composer.current.session;
            setTimeout(() => {
                if (
                    mounted.current &&
                    session === composer.current.session &&
                    valueRef.current &&
                    useAuthStore.getState().currentUser?.uid === currentUser.uid
                ) {
                    // Fast input can arrive before this deferred focus callback.
                    flushDraft();
                    const draft = getCardDraftStore().getDraft(titleDraftScope);
                    valueRef.current.value = draft?.content ?? "";
                    const body = draft?.description ?? "";
                    Object.assign(composer.current, { title: draft?.content ?? "", description: body });
                    setDescription(body);
                    // Opening a new composer session supersedes a previous in-flight request.
                    if (draft) saveFields(draft.content, body);
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
            if (useAuthStore.getState().currentUser?.uid !== currentUser.uid || composer.current.submitting) return;
            const submittedScope = { ...titleDraftScope };
            const submittedSession = composer.current;
            const session = submittedSession.session;
            const identityVersion = useAuthStore.getState().getIdentityVersion();
            const body = submittedSession.description;
            // Preserve the raw title draft even though quick creation trims its submitted title.
            saveTitleDraft(valueRef.current?.value ?? value);
            flushDraft();
            const submittedRequestUID = submittedSession.requestUID;
            submittedSession.submitting = true;
            setIsValidating(true);
            const ownsComposer = () =>
                mounted.current &&
                composer.current === submittedSession &&
                composer.current.session === session &&
                useAuthStore.getState().currentUser?.uid === submittedScope.userUID &&
                useAuthStore.getState().getIdentityVersion() === identityVersion;

            const promise = createCardMutateAsync({
                project_uid: project.uid,
                project_column_uid: column.uid,
                title: value,
                ...(body.length ? { description: { content: body } } : {}),
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
                    // Flush any later edit before comparing the submitted revision with storage.
                    flushDraft();
                    const draft = getCardDraftStore().getDraft(submittedScope);
                    const matchingDraft = draft?.request_uid === submittedRequestUID;
                    if (
                        useAuthStore.getState().currentUser?.uid === submittedScope.userUID &&
                        useAuthStore.getState().getIdentityVersion() === identityVersion &&
                        matchingDraft
                    ) {
                        getCardDraftStore().clearDraft(submittedScope);
                    }
                    if (!ownsComposer() || !matchingDraft || submittedSession.requestUID !== submittedRequestUID) {
                        return t("successes.Card added successfully.");
                    }
                    Object.assign(submittedSession, { title: "", description: "", requestUID: "" });
                    setDescription("");
                    if (valueRef.current) valueRef.current.value = "";
                    // Card creation stays lightweight: insert into the board without opening the viewer.
                    // The user opens the viewer only through an explicit card or widget interaction.
                    toLastPage();
                    scrollToCreatedCard(uid);
                    setIsEditing(() => false);
                    endCallback();
                    return t("successes.Card added successfully.");
                },
                finally: () => {
                    submittedSession.submitting = false;
                    if (ownsComposer()) setIsValidating(false);
                },
            });
        },
    });

    useLayoutEffect(() => {
        flushDraft();
        const draft = useAuthStore.getState().currentUser?.uid === currentUser.uid ? getCardDraftStore().getDraft(titleDraftScope) : null;
        composer.current = {
            scopeKey,
            session: crypto.randomUUID(),
            title: draft?.content ?? "",
            description: draft?.description ?? "",
            requestUID: draft?.request_uid ?? "",
            submitting: false,
        };
        if (valueRef.current) valueRef.current.value = composer.current.title;
        setDescription(composer.current.description);
        setIsValidating(false);
        // The exact scope key includes actor, project and column.
    }, [scopeKey, authUID, accountVersion, flushDraft]);

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
                description,
                saveDescriptionDraft,
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
