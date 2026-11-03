import { createContext, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createMemoryRouter, resolvePath, useLocation, useNavigate, type Location, type NavigateOptions, type To } from "react-router";

interface IPageNavigationScope {
    location: Location;
    navigate: (to: To | number, options?: NavigateOptions) => void;
}
const PageNavigationScopeContext = createContext<IPageNavigationScope | undefined>(undefined);

// Each pane owns a memory history. The focused pane follows the native URL.
export function PageNavigationScope({
    initialEntry,
    basePath,
    children,
    syncNative = false,
    onLocationChange,
}: {
    initialEntry: string;
    basePath: string;
    children: React.ReactNode;
    syncNative?: boolean;
    onLocationChange?: (location: Location) => void;
}) {
    const outerNavigate = useNavigate();
    const outerLocation = useLocation();
    const [router] = useState(() => createMemoryRouter([{ path: "*", element: null }], { initialEntries: [initialEntry] }));
    const memoryLocation = useSyncExternalStore(router.subscribe, () => router.state.location);
    // Keep one location identity per pane across focus changes. Native changes
    // are mirrored only when the native history changes, never in response to
    // a local draft update that has not reached the native router yet.
    const location = memoryLocation;
    const wasNative = useRef(syncNative);
    const pendingFocusProject = useRef<string | undefined>(undefined);
    const pendingNativeEntry = useRef<string | undefined>(undefined);
    useEffect(() => {
        if (syncNative && !wasNative.current) pendingFocusProject.current = router.state.location.pathname.split("/")[2];
        wasNative.current = syncNative;
        if (!syncNative) {
            pendingFocusProject.current = undefined;
            return;
        }
        const nativeEntry = outerLocation.pathname + outerLocation.search + outerLocation.hash;
        if (pendingNativeEntry.current) {
            if (nativeEntry !== pendingNativeEntry.current) return;
            pendingNativeEntry.current = undefined;
        }
        // Browser history may commit after the pane state update. Until the
        // focus target arrives, the previous pane's URL cannot own this pane.
        if (pendingFocusProject.current) {
            if (outerLocation.pathname.split("/")[2] !== pendingFocusProject.current) return;
            pendingFocusProject.current = undefined;
        }
        if (
            syncNative &&
            (router.state.location.pathname !== outerLocation.pathname ||
                router.state.location.search !== outerLocation.search ||
                router.state.location.hash !== outerLocation.hash)
        ) {
            void router.navigate(outerLocation.pathname + outerLocation.search + outerLocation.hash, { replace: true, state: outerLocation.state });
        }
    }, [syncNative, outerLocation, router]);
    useEffect(() => {
        onLocationChange?.(location);
    }, [location, onLocationChange]);
    const value = useMemo<IPageNavigationScope>(
        () => ({
            location,
            navigate: (to, options) => {
                if (syncNative) {
                    if (typeof to === "number") void outerNavigate(to);
                    else {
                        const path = resolvePath(to, location.pathname);
                        pendingNativeEntry.current = path.pathname + path.search + path.hash;
                        void router.navigate(path, options);
                        void outerNavigate(path, options);
                    }
                    return;
                }
                if (typeof to === "number") {
                    void router.navigate(to);
                    return;
                }
                const path = resolvePath(to, location.pathname);
                if (path.pathname === basePath || path.pathname.startsWith(`${basePath}/`)) void router.navigate(path, options);
                else void outerNavigate(path, options);
            },
        }),
        [basePath, location, outerNavigate, router, syncNative]
    );
    return <PageNavigationScopeContext.Provider value={value}>{children}</PageNavigationScopeContext.Provider>;
}
export const usePageNavigationScope = () => useContext(PageNavigationScopeContext);
