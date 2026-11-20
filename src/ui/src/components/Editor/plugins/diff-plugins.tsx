/* eslint-disable @typescript-eslint/no-explicit-any */
import { createSlatePlugin } from "platejs";
import { type PlateLeafProps, PlateLeaf, toPlatePlugin } from "platejs/react";
import { type DiffOperation, DiffUpdate, withGetFragmentExcludeDiff } from "@platejs/diff";
import { cn } from "@/core/utils/ComponentUtils";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

const diffOperationColors: Record<DiffOperation["type"], string> = {
    delete: "[&>*]:bg-destructive/70 [&_*]:line-through",
    insert: "[&>*]:bg-green-500/70 dark:[&>*]:bg-green-700/70",
    update: "[&>*]:bg-indigo-600/70 dark:[&>*]:bg-indigo-800/70",
};

const describeUpdate = ({ newProperties, properties }: DiffUpdate, t: TFunction) => {
    const addedProps: string[] = [];
    const removedProps: string[] = [];
    const updatedProps: string[] = [];

    Object.keys(newProperties).forEach((key) => {
        const oldValue = properties[key];
        const newValue = newProperties[key];

        if (oldValue === undefined) {
            addedProps.push(key);

            return;
        }
        if (newValue === undefined) {
            removedProps.push(key);

            return;
        }

        updatedProps.push(key);
    });

    const descriptionParts = [];

    if (addedProps.length > 0) {
        descriptionParts.push(t("editor.Added {properties}", { properties: addedProps.join(", ") }));
    }
    if (removedProps.length > 0) {
        descriptionParts.push(t("editor.Removed {properties}", { properties: removedProps.join(", ") }));
    }
    if (updatedProps.length > 0) {
        updatedProps.forEach((key) => {
            descriptionParts.push(
                t("editor.Updated {property} from {previous} to {next}", {
                    property: key,
                    previous: String(properties[key]),
                    next: String(newProperties[key]),
                })
            );
        });
    }

    return descriptionParts.join("\n");
};

const DiffPlugin = toPlatePlugin(
    createSlatePlugin({
        key: "diff",
        node: { isLeaf: true },
    }).overrideEditor(withGetFragmentExcludeDiff),
    {
        render: {
            node: DiffLeaf,
            aboveNodes: () =>
                function DiffAboveNodes({ children, editor, element }) {
                    const [t] = useTranslation();
                    if (!element.diff) return children;

                    const diffOperation = element.diffOperation as DiffOperation;

                    const label = (
                        {
                            delete: t("editor.deletion"),
                            insert: t("editor.insertion"),
                            update: t("editor.update"),
                        } as any
                    )[diffOperation.type];

                    const Component = editor.api.isInline(element) ? "span" : "div";

                    return (
                        <Component
                            className={cn(
                                diffOperationColors[diffOperation.type],
                                "[&_.slate-table]:ml-0 [&_.slate-table]:py-0 [&_td]:bg-background/20"
                            )}
                            aria-label={label}
                        >
                            {children}
                        </Component>
                    );
                },
        },
    }
);

function DiffLeaf({ children, ...props }: PlateLeafProps) {
    const [t] = useTranslation();
    const diffOperation = props.leaf.diffOperation as DiffOperation;

    return (
        <PlateLeaf
            {...props}
            className={diffOperationColors[diffOperation.type]}
            attributes={{
                ...props.attributes,
                title: diffOperation.type === "update" ? describeUpdate(diffOperation, t) : undefined,
            }}
        >
            {children}
        </PlateLeaf>
    );
}

export const diffPlugins = [DiffPlugin];
