import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";

/** React DND's default context shares one manager across editor and tree portals. */
export default function SharedDndProvider({ children }: { children: React.ReactNode }) {
    return <DndProvider backend={HTML5Backend}>{children}</DndProvider>;
}
