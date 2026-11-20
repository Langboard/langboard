"use client";

import { KEYS } from "platejs";
import { BlockPlaceholderPlugin } from "platejs/react";

export const BlockPlaceholderKit = [
    BlockPlaceholderPlugin.configure({
        options: {
            className: "before:absolute before:cursor-text before:text-muted-foreground/80 before:content-[attr(placeholder)]",
            placeholders: {
                [KEYS.p]: "",
            },
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            query: ({ path }: { path: any }) => {
                return path.length === 1;
            },
        },
    }),
];
