import { createContext, useContext, useId, useMemo, useState } from "react";

// Keep the standalone board contract; each mounted workspace owns its own viewport.
const BoardViewportContext = createContext({
    scrollportId: "board-scrollport",
    viewport: null as HTMLDivElement | null,
    registerViewport: (_node: HTMLDivElement | null) => {},
});

export function BoardViewportProvider({ children }: { children: React.ReactNode }) {
    const id = useId();
    const [viewport, registerViewport] = useState<HTMLDivElement | null>(null);
    const value = useMemo(() => ({ scrollportId: `board-scrollport-${id}`, viewport, registerViewport }), [id, viewport]);
    return <BoardViewportContext.Provider value={value}>{children}</BoardViewportContext.Provider>;
}

export const useBoardViewport = () => useContext(BoardViewportContext);
export const useBoardViewportId = () => useBoardViewport().scrollportId;
