import { lazy, type ComponentType } from "react";

// Cache public component code only. Route guards still own mounting and data access.
export function preloadableRoutePage(load: () => Promise<{ default: ComponentType }>) {
    let ready: ComponentType | undefined;
    const preload = async () => {
        const module = await load();
        ready = module.default;
        return module;
    };
    const LazyPage = lazy(preload);
    function Page() {
        const Component = ready ?? LazyPage;
        return <Component />;
    }
    return { Page, preload };
}
