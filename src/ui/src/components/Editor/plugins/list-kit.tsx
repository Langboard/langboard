"use client";

import { ListPlugin } from "@platejs/list/react";
import { KEYS } from "platejs";
import { IndentKit } from "@/components/Editor/plugins/indent-kit";
import { BlockList } from "@/components/plate-ui/block-list";

export const ListKit = [
    ...IndentKit,
    ListPlugin.configure({
        inject: {
            targetPlugins: [...KEYS.heading, KEYS.p, KEYS.blockquote, KEYS.codeBlock, KEYS.img],
        },
        render: {
            belowNodes: BlockList,
        },
    }),
];
