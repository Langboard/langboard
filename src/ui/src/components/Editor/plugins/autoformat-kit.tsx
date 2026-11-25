"use client";

import { CODE_DRAWING_TYPE_ARRAY, type CodeDrawingType, VIEW_MODE } from "@platejs/code-drawing";
import {
    createBlockStartInputRule,
    createSlatePlugin,
    createTextSubstitutionInputRule,
    KEYS,
    type SlateEditor,
    type TextSubstitutionPattern,
} from "platejs";

const enabled = ({ editor }: { editor: SlateEditor }) => !editor.api.some({ match: { type: editor.getType(KEYS.codeBlock) } });

// Preserve the existing symbol substitutions using Plate's native input-rule runtime.
const patterns: TextSubstitutionPattern[] = [
    {
        match: String.fromCharCode(34),
        format: ["“", "”"],
    },
    {
        match: "'",
        format: ["‘", "’"],
    },
    {
        match: "--",
        format: "—",
    },
    {
        match: "...",
        format: "…",
    },
    {
        match: ">>",
        format: "»",
    },
    {
        match: "<<",
        format: "«",
    },
    {
        match: ["(tm)", "(TM)"],
        format: "™",
    },
    {
        match: ["(r)", "(R)"],
        format: "®",
    },
    {
        match: ["(c)", "(C)"],
        format: "©",
    },
    {
        match: "&trade;",
        format: "™",
    },
    {
        match: "&reg;",
        format: "®",
    },
    {
        match: "&copy;",
        format: "©",
    },
    {
        match: "&sect;",
        format: "§",
    },
    {
        match: "->",
        format: "→",
    },
    {
        match: "<-",
        format: "←",
    },
    {
        match: "=>",
        format: "⇒",
    },
    {
        match: ["<=", "≤="],
        format: "⇐",
    },
    {
        match: "!>",
        format: "≯",
    },
    {
        match: "!<",
        format: "≮",
    },
    {
        match: ">=",
        format: "≥",
    },
    {
        match: "<=",
        format: "≤",
    },
    {
        match: "!>=",
        format: "≱",
    },
    {
        match: "!<=",
        format: "≰",
    },
    {
        match: "!=",
        format: "≠",
    },
    {
        match: "==",
        format: "≡",
    },
    {
        match: ["!==", "≠="],
        format: "≢",
    },
    {
        match: "~=",
        format: "≈",
    },
    {
        match: "!~=",
        format: "≉",
    },
    {
        match: "+-",
        format: "±",
    },
    {
        match: "%%",
        format: "‰",
    },
    {
        match: ["%%%", "‰%"],
        format: "‱",
    },
    // Bare slashes belong to plain text, dates and paths. Use the equation editor for fractions.
    {
        match: "^o",
        format: "°",
    },
    {
        match: "^+",
        format: "⁺",
    },
    {
        match: "^-",
        format: "⁻",
    },
    {
        match: "~+",
        format: "₊",
    },
    {
        match: "~-",
        format: "₋",
    },
    {
        match: "^0",
        format: "⁰",
    },
    {
        match: "^1",
        format: "¹",
    },
    {
        match: "^2",
        format: "²",
    },
    {
        match: "^3",
        format: "³",
    },
    {
        match: "^4",
        format: "⁴",
    },
    {
        match: "^5",
        format: "⁵",
    },
    {
        match: "^6",
        format: "⁶",
    },
    {
        match: "^7",
        format: "⁷",
    },
    {
        match: "^8",
        format: "⁸",
    },
    {
        match: "^9",
        format: "⁹",
    },
    {
        match: "~0",
        format: "₀",
    },
    {
        match: "~1",
        format: "₁",
    },
    {
        match: "~2",
        format: "₂",
    },
    {
        match: "~3",
        format: "₃",
    },
    {
        match: "~4",
        format: "₄",
    },
    {
        match: "~5",
        format: "₅",
    },
    {
        match: "~6",
        format: "₆",
    },
    {
        match: "~7",
        format: "₇",
    },
    {
        match: "~8",
        format: "₈",
    },
    {
        match: "~9",
        format: "₉",
    },
];

export const AutoformatKit = [
    createSlatePlugin({
        key: "editorShortcuts",
        inputRules: [
            createTextSubstitutionInputRule({ enabled, patterns }),
            createTextSubstitutionInputRule({ enabled, patterns: [{ match: "[[", format: "{{" }] }),
            createBlockStartInputRule({
                enabled,
                match: "$$e",
                trigger: "q",
                node: KEYS.equation,
                apply: ({ editor }, match) => {
                    editor.tf.delete({ at: match.range });
                    editor.tf.setNodes({ type: KEYS.equation });
                    editor.tf.insertNodes({ type: KEYS.p, children: [{ text: "" }] });
                    return true;
                },
            }),
            ...CODE_DRAWING_TYPE_ARRAY.map(({ value: drawingType }) =>
                createBlockStartInputRule({
                    enabled,
                    match: `$$${drawingType.slice(0, -1)}`,
                    trigger: drawingType.slice(-1),
                    node: KEYS.codeDrawing,
                    apply: ({ editor }, match) => {
                        editor.tf.delete({ at: match.range });
                        editor.tf.setNodes({
                            type: KEYS.codeDrawing,
                            data: { drawingType: drawingType as unknown as CodeDrawingType, drawingMode: VIEW_MODE.Both, code: "" },
                        });
                        return true;
                    },
                })
            ),
        ],
    }),
];
