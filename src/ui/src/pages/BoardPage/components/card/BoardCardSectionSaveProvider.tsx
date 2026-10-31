import { createContext, useCallback, useContext, useMemo, useRef } from "react";
import type { IEditorContent } from "@/core/models/Base";

type TBoardCardUnsavedSection = "deadline" | "description" | "title";
export interface IBoardCardDetailsPatch {
    deadline_at?: Date | "";
    description?: IEditorContent;
    title?: string;
}

type TSectionHandler = () => IBoardCardDetailsPatch | null | void | Promise<IBoardCardDetailsPatch | null | void>;
type TSectionCancelHandler = () => void;

interface IBoardCardSectionSaveActionsContext {
    registerSectionSaveHandler: (section: TBoardCardUnsavedSection, handler: TSectionHandler, readDraft?: () => string) => () => void;
    getSectionDraftSnapshot: () => string;
    registerSectionCancelHandler: (section: TBoardCardUnsavedSection, handler: TSectionCancelHandler) => () => void;
    cancelSections: () => void;
    saveSections: () => Promise<IBoardCardDetailsPatch | false>;
}

const BoardCardSectionSaveActionsContext = createContext<IBoardCardSectionSaveActionsContext | null>(null);

export const BoardCardSectionSaveProvider = ({ children }: { children: React.ReactNode }) => {
    const saveHandlersRef = useRef<Partial<Record<TBoardCardUnsavedSection, TSectionHandler>>>({});
    const draftReadersRef = useRef<Partial<Record<TBoardCardUnsavedSection, () => string>>>({});
    const cancelHandlersRef = useRef<Partial<Record<TBoardCardUnsavedSection, TSectionCancelHandler>>>({});

    const registerSectionSaveHandler = useCallback((section: TBoardCardUnsavedSection, handler: TSectionHandler, readDraft?: () => string) => {
        draftReadersRef.current[section] = readDraft;
        saveHandlersRef.current[section] = handler;

        return () => {
            if (saveHandlersRef.current[section] === handler) {
                delete saveHandlersRef.current[section];
                delete draftReadersRef.current[section];
            }
        };
    }, []);

    const registerSectionCancelHandler = useCallback((section: TBoardCardUnsavedSection, handler: TSectionCancelHandler) => {
        cancelHandlersRef.current[section] = handler;

        return () => {
            if (cancelHandlersRef.current[section] === handler) {
                delete cancelHandlersRef.current[section];
            }
        };
    }, []);

    const getSectionDraftSnapshot = useCallback(
        () =>
            JSON.stringify(
                Object.entries(draftReadersRef.current)
                    .sort(([left], [right]) => left.localeCompare(right))
                    .map(([section, read]) => [section, read?.()])
            ),
        []
    );

    const cancelSections = useCallback(() => {
        const sections = Object.keys(cancelHandlersRef.current) as TBoardCardUnsavedSection[];

        sections.forEach((section) => {
            try {
                cancelHandlersRef.current[section]?.();
            } catch {
                // Keep card edit cancellation usable even if a section cleanup fails.
            }
        });
    }, []);

    const saveSections = useCallback(async () => {
        const sections = Object.keys(saveHandlersRef.current) as TBoardCardUnsavedSection[];
        const details: IBoardCardDetailsPatch = {};

        for (const section of sections) {
            const handler = saveHandlersRef.current[section];
            if (!handler) {
                return false;
            }

            try {
                const result = await handler();
                if (result) {
                    Object.assign(details, result);
                }
            } catch {
                return false;
            }
        }

        return details;
    }, []);

    const actionsValue = useMemo(
        () => ({
            cancelSections,
            getSectionDraftSnapshot,
            registerSectionCancelHandler,
            registerSectionSaveHandler,
            saveSections,
        }),
        [cancelSections, getSectionDraftSnapshot, registerSectionCancelHandler, registerSectionSaveHandler, saveSections]
    );

    return <BoardCardSectionSaveActionsContext.Provider value={actionsValue}>{children}</BoardCardSectionSaveActionsContext.Provider>;
};

export const useBoardCardSectionSaveActions = () => {
    const context = useContext(BoardCardSectionSaveActionsContext);
    if (!context) {
        throw new Error("useBoardCardSectionSaveActions must be used within BoardCardSectionSaveProvider");
    }

    return context;
};
