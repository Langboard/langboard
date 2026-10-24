"use client";

import SharedDndProvider from "@/components/SharedDndProvider";
import { DndPlugin } from "@platejs/dnd";
import { PlaceholderPlugin } from "@platejs/media/react";
import { BlockDraggable } from "@/components/plate-ui/block-draggable";

export const DndKit = [
    DndPlugin.configure({
        options: {
            enableScroller: true,
            onDropFiles: ({ dragItem, editor, target }) => {
                editor.getTransforms(PlaceholderPlugin).insert.media(dragItem.files, { at: target, nextBlock: false });
            },
        },
        render: {
            aboveNodes: BlockDraggable,
            aboveSlate: ({ children }) => <SharedDndProvider>{children}</SharedDndProvider>,
        },
    }),
];
