import { createRoot } from "react-dom/client";
import { MemoryRouter, useNavigate, useSearchParams } from "react-router";
import useSearchFilters, { type ISearchFilterMap } from "@/core/hooks/useSearchFilters";
function Pane({ name }: { name: string }) {
    const navigate = useNavigate();
    const [, setParams] = useSearchParams();
    const filters = useSearchFilters<ISearchFilterMap>({ filterKeys: ["columns", "keyword"] });
    return (
        <section aria-label={name}>
            <output>{JSON.stringify(filters.filters)}</output>
            <button onClick={() => setParams({ filters: "columns:next,keyword:hello%2520world" })}>Change filter</button>
            <button onClick={() => navigate(-1)}>Back</button>
            <button onClick={() => filters.forceUpdate()}>Refresh filter</button>
        </section>
    );
}
createRoot(document.getElementById("root")!).render(
    <>
        <MemoryRouter initialEntries={["/board/left?filters=columns:left"]}>
            <Pane name="Left pane" />
        </MemoryRouter>
        <MemoryRouter initialEntries={["/board/right?filters=columns:right"]}>
            <Pane name="Right pane" />
        </MemoryRouter>
    </>
);
