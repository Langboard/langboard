import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useLocation, useNavigate } from "react-router";
import { DashboardStyledLayout } from "@/components/Layout";
import type { TDashboardStyledLayoutProps } from "@/components/Layout/DashboardStyledLayout";
import { useAuth } from "@/core/providers/AuthProvider";
import { persistBoardSplit, restoreBoardSplit } from "./BoardSplitPersistence";
import { ScreenMap } from "@/core/utils/VariantUtils";
import { BoardSplitContext, useBoardSplitContext, type IBoardPane } from "./BoardSplitContext";

export { useBoardSplitContext } from "./BoardSplitContext";

export const BoardSplitPaneContext = createContext<string | undefined>(undefined);
const BoardSplitSidebarSlotContext = createContext<HTMLDivElement | null>(null);
export const useBoardSplitSidebarSlot = () => useContext(BoardSplitSidebarSlotContext);
export const BOARD_PANE_BEFORE_CLOSE = "langboard:board-pane-before-close";
export const useBoardSplitPane = () => useContext(BoardSplitPaneContext);

export function BoardSplitProvider({ children }: { children: React.ReactNode }) {
    const { currentUser } = useAuth();
    return (
        <UserBoardSplitProvider key={currentUser?.uid ?? "anonymous"} userUID={currentUser?.uid}>
            {children}
        </UserBoardSplitProvider>
    );
}

function UserBoardSplitProvider({ children, userUID }: { children: React.ReactNode; userUID?: string }) {
    const location = useLocation();
    const navigate = useNavigate();
    const [panes, setPanes] = useState<IBoardPane[]>(() => {
        const entry = location.pathname + location.search + location.hash;
        let entries = [entry];
        try {
            entries = restoreBoardSplit(window.sessionStorage, userUID, entry, window.innerWidth >= ScreenMap.size.md);
        } catch {
            // Storage access itself can be denied.
        }
        return entries.map((initialEntry, index) => ({ id: `board-pane-${index}`, initialEntry }));
    });
    const currentPanes = useRef(panes);
    currentPanes.current = panes;
    const [preferredFocus, setFocusedPane] = useState("board-pane-0");
    const pendingFocus = useRef<string | undefined>(undefined);
    const nextID = useRef(panes.length);
    const locations = useRef(new Map<string, string>());
    const layouts = useRef(new Map<string, TDashboardStyledLayoutProps>());
    const listeners = useRef(new Set<() => void>());
    const lastLayout = useRef<TDashboardStyledLayoutProps | undefined>(undefined);
    // Native navigation (including Back) to an already open board transfers
    // focus before either scoped location renders; it never duplicates a board.
    const routeProjectUID = location.pathname.split("/")[2];
    const nativeOwner = panes.find((pane) => (locations.current.get(pane.id) ?? pane.initialEntry).split(/[/?#]/)[2] === routeProjectUID);
    // Keep explicit pane focus until BrowserRouter publishes its target URL.
    // Input in that interval must already use the focused native navigator.
    if (pendingFocus.current === nativeOwner?.id) pendingFocus.current = undefined;
    const focusedPane = pendingFocus.current ?? nativeOwner?.id ?? preferredFocus;
    useEffect(() => {
        if (focusedPane !== preferredFocus) setFocusedPane(focusedPane);
    }, [focusedPane, preferredFocus]);
    const focusedRef = useRef(focusedPane);
    focusedRef.current = focusedPane;
    const publishLayout = useCallback((id: string, props?: TDashboardStyledLayoutProps) => {
        if (props) {
            layouts.current.set(id, props);
            if (focusedRef.current === id || !lastLayout.current) lastLayout.current = props;
        } else layouts.current.delete(id);
        listeners.current.forEach((listener) => listener());
    }, []);
    const subscribeLayout = useCallback((listener: () => void) => {
        listeners.current.add(listener);
        return () => {
            listeners.current.delete(listener);
        };
    }, []);
    const getLayout = useCallback(() => layouts.current.get(focusedRef.current) ?? lastLayout.current, []);
    const persist = useCallback(() => {
        try {
            persistBoardSplit(
                window.sessionStorage,
                userUID,
                panes.map((pane) => locations.current.get(pane.id) ?? pane.initialEntry),
                window.innerWidth >= ScreenMap.size.md
            );
        } catch {
            // Storage access itself can be denied.
        }
    }, [panes, userUID]);
    useEffect(persist, [persist]);
    const rememberLocation = useCallback(
        (id: string, entry: string) => {
            if (!panes.some((pane) => pane.id === id)) return;
            locations.current.set(id, entry);
            persist();
        },
        [panes, persist]
    );
    const focus = useCallback(
        (id: string) => {
            const entry = locations.current.get(id) ?? panes.find((pane) => pane.id === id)?.initialEntry;
            if (!entry || id === focusedRef.current) return;
            pendingFocus.current = id;
            focusedRef.current = id;
            void navigate(entry, { replace: true });
            setFocusedPane(id);
        },
        [navigate, panes]
    );
    const open = useCallback(
        (projectUID: string) => {
            if (window.innerWidth < ScreenMap.size.md || !/^[\w-]+$/.test(projectUID)) return;
            const existing = panes.find((pane) => (locations.current.get(pane.id) ?? pane.initialEntry).split(/[/?#]/)[2] === projectUID);
            if (existing) {
                focus(existing.id);
                return;
            }
            if (panes.length >= 2) return;
            setPanes((current) =>
                current.length >= 2 ? current : [...current, { id: `board-pane-${nextID.current++}`, initialEntry: `/board/${projectUID}` }]
            );
        },
        [focus, panes]
    );
    const close = useCallback(
        (id: string, force = false) => {
            // Authorization loss must discard restoration hints before navigation,
            // even when this is the last pane or another denial arrived this render.
            if (force) {
                try {
                    persistBoardSplit(window.sessionStorage, userUID, [], window.innerWidth >= ScreenMap.size.md);
                } catch {
                    // Storage access itself can be denied.
                }
            }
            const current = currentPanes.current;
            if (current.length < 2 || !current.some((pane) => pane.id === id)) return false;
            if (!force && !window.dispatchEvent(new CustomEvent(BOARD_PANE_BEFORE_CLOSE, { cancelable: true, detail: { paneID: id } }))) return false;
            const survivor = current.find((pane) => pane.id !== id)!;
            const entry = locations.current.get(survivor.id) ?? survivor.initialEntry;
            pendingFocus.current = survivor.id;
            focusedRef.current = survivor.id;
            currentPanes.current = current.filter((pane) => pane.id !== id);
            setPanes(currentPanes.current);
            locations.current.delete(id);
            void navigate(entry, { replace: true });
            setFocusedPane(survivor.id);
            return true;
        },
        [navigate, userUID]
    );
    const value = useMemo(
        () => ({ panes, focusedPane, open, close, focus, rememberLocation, publishLayout, subscribeLayout, getLayout }),
        [panes, focusedPane, open, close, focus, rememberLocation, publishLayout, subscribeLayout, getLayout]
    );
    return <BoardSplitContext.Provider value={value}>{children}</BoardSplitContext.Provider>;
}

export function BoardSplitChrome({ children }: { children: React.ReactNode }) {
    const split = useBoardSplitContext()!;
    const props = useSyncExternalStore(split.subscribeLayout, split.getLayout);
    const [sidebarSlot, setSidebarSlot] = useState<HTMLDivElement | null>(null);
    const slotElement = useMemo(() => <div ref={setSidebarSlot} className="size-full" />, []);
    return (
        <DashboardStyledLayout workbench {...props} workbenchContext={props?.workbenchContext ? slotElement : undefined} className="!h-full !p-0">
            <BoardSplitSidebarSlotContext.Provider value={sidebarSlot}>{children}</BoardSplitSidebarSlotContext.Provider>
        </DashboardStyledLayout>
    );
}
