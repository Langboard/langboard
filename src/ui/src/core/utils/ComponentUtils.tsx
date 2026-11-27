/* eslint-disable @typescript-eslint/no-explicit-any */
import { Utils } from "@langboard/core/utils";
import clsx, { ClassValue } from "clsx";
import { cloneElement, forwardRef } from "react";
import { createRoot } from "react-dom/client";
import { twMerge } from "tailwind-merge";

type TPossibleRef<T> = React.Ref<T> | undefined;

export function cn(...inputs: ClassValue[]): string {
    return twMerge(clsx(inputs.filter(Boolean).join(" ")));
}

function setRef<T>(ref: TPossibleRef<T>, value: T) {
    if (Utils.Type.isFunction(ref)) {
        ref(value);
    } else if (!Utils.Type.isNullOrUndefined(ref)) {
        ref.current = value;
    }
}

export function composeRefs<T>(...refs: TPossibleRef<T>[]) {
    return (node: T) => refs.forEach((ref) => setRef(ref, node));
}

export function withProps<T extends React.ElementType>(Component: T, defaultProps: Partial<React.ComponentPropsWithoutRef<T>>) {
    const ComponentWithClassName = Component as React.FC<{ className?: string }>;

    return forwardRef<React.ComponentRef<T>, React.ComponentPropsWithoutRef<T>>(function ExtendComponent(props, ref) {
        const newProps: any = { ...defaultProps, ...props };
        const className = cn((defaultProps as any).className, (props as any).className);

        if (className) {
            newProps.className = className;
        }

        return <ComponentWithClassName ref={ref} {...newProps} />;
    });
}

export const measureComponentHeight = (element: React.ReactElement): Promise<number> =>
    new Promise((resolve) => {
        const rootElem = document.createElement("div");
        setElementStyles(rootElem, {
            position: "absolute !important",
            visibility: "hidden !important",
            zIndex: "-1 !important",
            height: "auto !important",
            width: "auto !important",
            top: "0 !important",
        });
        document.body.appendChild(rootElem);
        const root = createRoot(rootElem);
        root.render(cloneElement(element));
        setTimeout(() => {
            const height = rootElem.clientHeight;
            document.body.removeChild(rootElem);
            resolve(height);
        });
    });

export const measureTextAreaHeight = (textarea: HTMLTextAreaElement): number => {
    const cloned = textarea.cloneNode(true) as HTMLTextAreaElement;
    cloned.value = textarea.value;
    setElementStyles(cloned, {
        width: `${textarea.offsetWidth}px`,
        height: "0px",
    });
    document.body.appendChild(cloned);
    const height = cloned.scrollHeight;
    document.body.removeChild(cloned);
    cloned.remove();
    return height;
};

export const setElementStyles = (elements: HTMLElement | HTMLElement[], styles: Record<string, string>) => {
    if (!Utils.Type.isArray(elements)) {
        elements = [elements];
    }

    Object.entries(styles).forEach(([key, value]) => {
        elements.forEach((element) => {
            element.style.setProperty(new Utils.String.Case(key).toKebab(), value);
        });
    });
};

export const selectAllText = (element: HTMLInputElement | HTMLTextAreaElement) => {
    element.selectionStart = 0;
    element.selectionEnd = element.value.length;
};

export const copyToClipboard = async (text: string) => {
    if (!navigator?.clipboard) {
        document.execCommand("copy");
        return;
    }

    try {
        await navigator.clipboard.writeText(text);
    } catch {
        document.execCommand("copy");
    }
};
