import { createContext, useContext, useMemo, useState, useSyncExternalStore } from "react";
import { createMemoryRouter, resolvePath, useNavigate, type Location, type NavigateOptions, type To } from "react-router";

interface IPageNavigationScope {
    location: Location;
    navigate: (to: To | number, options?: NavigateOptions) => void;
}

const PageNavigationScopeContext = createContext<IPageNavigationScope | undefined>(undefined);

// A pane owns a native memory history, without nesting a Router inside the app's Router.
// There are no loaders or external history listeners; unsubscribing releases the owned store.
export function PageNavigationScope({ initialEntry, basePath, children }: { initialEntry: string; basePath: string; children: React.ReactNode }) {
    const outerNavigate = useNavigate();
    const [router] = useState(() => createMemoryRouter([{ path: "*", element: null }], { initialEntries: [initialEntry] }));
    const location = useSyncExternalStore(router.subscribe, () => router.state.location);
    const value = useMemo<IPageNavigationScope>(
        () => ({
            location,
            navigate: (to, options) => {
                if (typeof to === "number") {
                    void router.navigate(to);
                    return;
                }
                const path = resolvePath(to, location.pathname);
                if (path.pathname === basePath || path.pathname.startsWith(`${basePath}/`)) {
                    void router.navigate(path, options);
                } else {
                    void outerNavigate(path, options);
                }
            },
        }),
        [basePath, location, outerNavigate, router]
    );
    return <PageNavigationScopeContext.Provider value={value}>{children}</PageNavigationScopeContext.Provider>;
}

export const usePageNavigationScope = () => useContext(PageNavigationScopeContext);
