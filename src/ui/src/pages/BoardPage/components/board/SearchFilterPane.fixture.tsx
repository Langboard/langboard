import { createRoot } from "react-dom/client";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { usePageNavigate } from "@/core/hooks/usePageNavigate";
import { usePageSearchParams } from "@/core/hooks/usePageSearchParams";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import useSearchFilters, { type ISearchFilterMap } from "@/core/hooks/useSearchFilters";
function Pane({ name }: { name: string }) {
    const navigate = usePageNavigate();
    const [, setParams] = usePageSearchParams();
    const filters = useSearchFilters<ISearchFilterMap>({ filterKeys: ["columns", "keyword"] });
    return (
        <section aria-label={name}>
            <output>{JSON.stringify(filters.filters)}</output>
            <button onClick={() => setParams({ filters: "columns:next,keyword:hello%2520world" })}>Change filter</button>
            <button onClick={() => navigate(-1)}>Back</button>
            <button onClick={() => navigate(1)}>Forward</button>
            <button
                onClick={() =>
                    setParams(
                        (params) => {
                            params.set("new-card", "1");
                            return params;
                        },
                        { replace: true }
                    )
                }
            >
                Compose
            </button>
            <button onClick={() => navigate("/dashboard/projects/all")}>Dashboard</button>
            <span data-pane-location>{usePageLocation().pathname + usePageLocation().search}</span>
            <button onClick={() => filters.forceUpdate()}>Refresh filter</button>
        </section>
    );
}
function OuterLocation() {
    return <span data-outer-location>{useLocation().pathname}</span>;
}
createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <MemoryRouter initialEntries={["/workspace"]}>
            <PageNavigationScope basePath="/board/left" initialEntry="/board/left?filters=columns:left">
                <Pane name="Left pane" />
            </PageNavigationScope>
            <PageNavigationScope basePath="/board/right" initialEntry="/board/right?filters=columns:right">
                <Pane name="Right pane" />
            </PageNavigationScope>
            <OuterLocation />
        </MemoryRouter>
    </StrictMode>
);
