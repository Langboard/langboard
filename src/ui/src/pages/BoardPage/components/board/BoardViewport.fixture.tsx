import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { useState, useCallback } from "react";
import { createRoot } from "react-dom/client";
import ScrollArea from "@/components/base/ScrollArea";
import Nav from "@/components/base/Floating/Nav";
import { BoardViewportProvider, useBoardViewport } from "@/core/providers/BoardViewportProvider";
import BoardMinimap from "./BoardMinimap";
import { BOARD_COLUMN_TOUCH_DND_ATTR } from "./BoardConstants";

function Viewport({ name }: { name: string }) {
    const { scrollportId: id, registerViewport } = useBoardViewport();
    const [node, setNode] = useState<HTMLDivElement | null>(null);
    const [loaded, setLoaded] = useState(() => !new URLSearchParams(window.location.search).has("deferred"));
    const register = useCallback(
        (node: HTMLDivElement | null) => {
            setNode(node);
            registerViewport(node);
        },
        [registerViewport]
    );
    return (
        <section aria-label={name} className="relative min-w-0 flex-1">
            {!loaded && <button onClick={() => setLoaded(true)}>Load {name}</button>}
            {loaded && (
                <ScrollArea.Root viewportId={id} viewportRef={register} className="h-96" viewportClassName="!overflow-x-auto !overflow-y-hidden">
                    <div className="flex w-max gap-4 p-4">
                        {Array.from({ length: 8 }, (_, index) => (
                            <div key={index} {...{ [BOARD_COLUMN_TOUCH_DND_ATTR]: `${name}-${index}` }} className="h-64 w-72 border">
                                {name} {index}
                            </div>
                        ))}
                    </div>
                    <ScrollArea.Bar orientation="horizontal" />
                </ScrollArea.Root>
            )}
            <BoardMinimap scrollable={node} scrollportId={id} />
            <Nav fixed items={[{ label: `${name} action`, icon: "pin", onClick: () => node?.scrollTo({ left: 400 }) }]} />
        </section>
    );
}

function Fixture() {
    const [left, setLeft] = useState(true);
    return (
        <>
            <button onClick={() => setLeft(false)}>Close left</button>
            <main className="flex w-full">
                {left && (
                    <BoardViewportProvider key="left">
                        <Viewport name="Left" />
                    </BoardViewportProvider>
                )}
                <BoardViewportProvider key="right">
                    <Viewport name="Right" />
                </BoardViewportProvider>
            </main>
        </>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
