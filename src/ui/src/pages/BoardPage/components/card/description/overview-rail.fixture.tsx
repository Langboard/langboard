import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import DescriptionOverviewRail from "./DescriptionOverviewRail";
import type { IDescriptionChunk } from "./descriptionChunks";
import "@/i18n";
import "@/assets/styles/main.css";

const chunks: IDescriptionChunk[] = Array.from({ length: 60 }, (_, index) => ({
    id: `section-${index}`,
    content: `Section ${index + 1}`,
    metadata: { type: "heading", heading: `Section ${index + 1}`, previewText: `Preview ${index + 1}`, textLength: 30, isHeavy: false },
}));
function Fixture() {
    const viewport = useRef<HTMLDivElement>(null);
    const [count, setCount] = useState(60);
    const [height, setHeight] = useState(400);
    const [rowHeight, setRowHeight] = useState(160);
    const [active, setActive] = useState(0);
    const [dark, setDark] = useState(false);
    return (
        <div className={dark ? "dark" : ""}>
            <main className="min-h-screen bg-background text-foreground">
                <nav className="flex flex-wrap gap-2">
                    <button onClick={() => setCount(2)}>Short</button>
                    <button onClick={() => setCount(5)}>Medium</button>
                    <button onClick={() => setCount(60)}>Long</button>
                    <button onClick={() => setHeight(height === 400 ? 560 : 400)}>Resize</button>
                    <button onClick={() => setRowHeight(rowHeight === 160 ? 240 : 160)}>Content resize</button>
                    <button onClick={() => setDark(!dark)}>Theme</button>
                </nav>
                <div data-card-content-frame className="relative mx-auto max-w-3xl pl-10" style={{ height }}>
                    <div
                        ref={viewport}
                        id="overview-rail-viewport"
                        data-card-content-viewport
                        className="h-full overflow-y-auto ring-1 ring-inset [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                        onScroll={() => setActive(Math.floor((viewport.current?.scrollTop ?? 0) / rowHeight))}
                    >
                        {chunks.slice(0, count).map((chunk, index) => (
                            <section key={chunk.id} data-section={index} style={{ height: rowHeight }} className="border-b p-4">
                                <h2>{chunk.content}</h2>
                                <p>Description content for native scrolling.</p>
                            </section>
                        ))}
                    </div>
                    <DescriptionOverviewRail
                        chunks={chunks.slice(0, count)}
                        activeIndex={active}
                        viewportHeight={height}
                        scrollParentRef={viewport}
                        onNavigate={(index) => {
                            if (viewport.current) viewport.current.scrollTop = index * rowHeight;
                        }}
                    />
                </div>
            </main>
        </div>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
