import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";

/** Keep one backend for this window across Explorer and editor remounts. */
export default function SharedDndProvider({ children }: { children: React.ReactNode }) {
    return (
        <DndProvider backend={HTML5Backend} context={window}>
            {children}
        </DndProvider>
    );
}
