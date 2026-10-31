import { useState } from "react";
import { createRoot } from "react-dom/client";
import ResizableSidebar from "./index";
import "@/i18n";
import "@/core/injection";
import "@/assets/styles/main.css";

function Fixture() {
    const [hidden, setHidden] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
    return (
        <>
            <button onClick={() => setHidden(!hidden)}>Toggle visibility</button>
            <button onClick={() => setCollapsed(!collapsed)}>Toggle owner state</button>
            <ResizableSidebar
                initialWidth={280}
                collapsed={collapsed}
                onCollapsedChange={(next, reason) => {
                    if (reason === "user") setCollapsed(next);
                }}
                hidden={hidden}
                floatingHidden
                widthCssVariable="--board-chat-sidebar-width"
                main={<main style={{ marginLeft: "var(--board-chat-sidebar-width)" }}>Overlay content</main>}
            >
                <p>Native sidebar content</p>
            </ResizableSidebar>
        </>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
