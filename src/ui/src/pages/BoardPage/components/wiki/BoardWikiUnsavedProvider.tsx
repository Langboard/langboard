import { createContext, useCallback, useContext, useMemo, useRef } from "react";

type TBoardWikiUnsavedSection = "content" | "title";
type TSectionHandler = () => void | Promise<void>;

interface IBoardWikiUnsavedActionsContext {
    markSectionDirty: (section: TBoardWikiUnsavedSection, dirty: bool) => void;
    resetSection: (section: TBoardWikiUnsavedSection) => void;
    resetAll: () => void;
    getHasUnsavedChanges: () => bool;
    registerSectionSaveHandler: (section: TBoardWikiUnsavedSection, handler: TSectionHandler) => () => void;
    registerSectionCancelHandler: (section: TBoardWikiUnsavedSection, handler: TSectionHandler) => () => void;
    saveDirtySections: () => Promise<bool>;
    cancelDirtySections: () => void;
}

const BoardWikiUnsavedActionsContext = createContext<IBoardWikiUnsavedActionsContext | null>(null);

export const BoardWikiUnsavedProvider = ({ children }: { children: React.ReactNode }) => {
    const sectionStateRef = useRef<Partial<Record<TBoardWikiUnsavedSection, bool>>>({});
    const hasUnsavedChangesRef = useRef(false);
    const saveHandlersRef = useRef<Partial<Record<TBoardWikiUnsavedSection, TSectionHandler>>>({});
    const cancelHandlersRef = useRef<Partial<Record<TBoardWikiUnsavedSection, TSectionHandler>>>({});

    const updateDirtyFlag = useCallback(() => {
        hasUnsavedChangesRef.current = Object.values(sectionStateRef.current).some(Boolean);
    }, []);

    const markSectionDirty = useCallback(
        (section: TBoardWikiUnsavedSection, dirty: bool) => {
            const next = sectionStateRef.current;
            if (!!next[section] === dirty) {
                return;
            }

            if (!dirty) {
                delete next[section];
            } else {
                next[section] = true;
            }

            updateDirtyFlag();
        },
        [updateDirtyFlag]
    );

    const resetSection = useCallback(
        (section: TBoardWikiUnsavedSection) => {
            const next = sectionStateRef.current;
            if (!next[section]) {
                return;
            }

            delete next[section];
            updateDirtyFlag();
        },
        [updateDirtyFlag]
    );

    const resetAll = useCallback(() => {
        sectionStateRef.current = {};
        hasUnsavedChangesRef.current = false;
    }, []);

    const registerSectionSaveHandler = useCallback((section: TBoardWikiUnsavedSection, handler: TSectionHandler) => {
        saveHandlersRef.current[section] = handler;

        return () => {
            if (saveHandlersRef.current[section] === handler) {
                delete saveHandlersRef.current[section];
            }
        };
    }, []);

    const registerSectionCancelHandler = useCallback((section: TBoardWikiUnsavedSection, handler: TSectionHandler) => {
        cancelHandlersRef.current[section] = handler;

        return () => {
            if (cancelHandlersRef.current[section] === handler) {
                delete cancelHandlersRef.current[section];
            }
        };
    }, []);

    const saveDirtySections = useCallback(async () => {
        const dirtySections = Object.keys(sectionStateRef.current) as TBoardWikiUnsavedSection[];
        let isSaved = true;

        for (const section of dirtySections) {
            const handler = saveHandlersRef.current[section];
            if (!handler) {
                isSaved = false;
                continue;
            }

            try {
                await handler();
            } catch {
                isSaved = false;
                continue;
            }

            if (sectionStateRef.current[section]) {
                isSaved = false;
            }
        }

        updateDirtyFlag();
        return isSaved;
    }, [updateDirtyFlag]);

    const cancelDirtySections = useCallback(() => {
        const dirtySections = Object.keys(sectionStateRef.current) as TBoardWikiUnsavedSection[];

        for (const section of dirtySections) {
            cancelHandlersRef.current[section]?.();
        }

        resetAll();
    }, [resetAll]);

    const actionsValue = useMemo(
        () => ({
            markSectionDirty,
            resetSection,
            resetAll,
            getHasUnsavedChanges: () => hasUnsavedChangesRef.current,
            registerSectionSaveHandler,
            registerSectionCancelHandler,
            saveDirtySections,
            cancelDirtySections,
        }),
        [markSectionDirty, resetSection, resetAll, registerSectionSaveHandler, registerSectionCancelHandler, saveDirtySections, cancelDirtySections]
    );

    return <BoardWikiUnsavedActionsContext.Provider value={actionsValue}>{children}</BoardWikiUnsavedActionsContext.Provider>;
};

export const useBoardWikiUnsavedActions = () => {
    const context = useContext(BoardWikiUnsavedActionsContext);
    if (!context) {
        throw new Error("useBoardWikiUnsavedActions must be used within BoardWikiUnsavedProvider");
    }

    return context;
};
