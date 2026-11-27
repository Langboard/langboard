"use client";

import type { TTagElement } from "platejs";
import type { PlateElementProps } from "platejs/react";
import { PlateElement, useFocused, useReadOnly, useSelected } from "platejs/react";
import { cn } from "@/core/utils/ComponentUtils";
import IconComponent from "@/components/base/IconComponent";
import CollaborativeUserLabel from "@/components/Collaborative/UserLabel";
import { MultiSelectPlugin } from "@platejs/tag/react";
import { Utils } from "@langboard/core/utils";

export type TTagElementProps = {
    createTagContent?: (props: TTagElement & { readOnly: bool }) => React.JSX.Element;
    itemRemovedCallback?: (item: TTagElement) => void;
};

export function TagElement(props: PlateElementProps<TTagElement> & TTagElementProps) {
    const { element, createTagContent, itemRemovedCallback, editor } = props;
    const selected = useSelected();
    const focused = useFocused();
    const readOnly = useReadOnly();
    const badgeBorderColor = Utils.Type.isString((element as { badgeBorderColor?: unknown }).badgeBorderColor)
        ? ((element as { badgeBorderColor?: string }).badgeBorderColor ?? "")
        : "";
    const badgeActorName = Utils.Type.isString((element as { badgeActorName?: unknown }).badgeActorName)
        ? ((element as { badgeActorName?: string }).badgeActorName ?? "")
        : "";
    const removeItem = (item: TTagElement) => {
        editor.getTransforms(MultiSelectPlugin).delete({
            at: editor.getApi(MultiSelectPlugin).findPath(item),
        });

        itemRemovedCallback?.(element);
    };

    const badge = (
        <div
            className={cn(
                "relative shrink-0 break-normal rounded-full border px-2.5 align-middle text-sm font-semibold transition-colors focus:outline-none",
                "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/60",
                selected && focused && "ring-2 ring-ring ring-offset-0",
                "flex items-center gap-1.5"
            )}
            style={
                badgeBorderColor
                    ? {
                          boxShadow: `inset 0 0 0 2px ${badgeBorderColor}`,
                      }
                    : undefined
            }
        >
            {badgeBorderColor && badgeActorName ? (
                <CollaborativeUserLabel
                    className="absolute left-0 top-0 max-w-full -translate-y-1/2 truncate whitespace-nowrap"
                    color={badgeBorderColor}
                    name={badgeActorName}
                />
            ) : null}
            {createTagContent ? createTagContent({ ...element, readOnly }) : element.value}
            {!readOnly && <IconComponent icon="x" size="4" className="hover:text-secondary-foreground/70" onClick={() => removeItem(element)} />}
        </div>
    );

    return (
        <PlateElement
            {...props}
            className="m-0.5 inline-flex cursor-pointer select-none"
            attributes={{
                ...props.attributes,
                draggable: true,
            }}
        >
            {badge}
            {props.children}
        </PlateElement>
    );
}
