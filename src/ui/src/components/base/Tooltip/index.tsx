/* eslint-disable @/max-len */
"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as React from "react";
import { cn } from "@/core/utils/ComponentUtils";

const DEFAULT_DURATION = 400;

const Provider = TooltipPrimitive.Provider;

function Root({
    delayDuration = DEFAULT_DURATION,
    skipDelayDuration,
    ...props
}: React.ComponentProps<typeof TooltipPrimitive.Root> & Pick<React.ComponentProps<typeof Provider>, "skipDelayDuration">) {
    return (
        <Provider delayDuration={delayDuration} skipDelayDuration={skipDelayDuration}>
            <TooltipPrimitive.Root {...props} />
        </Provider>
    );
}

const Trigger = TooltipPrimitive.Trigger;

const Portal = TooltipPrimitive.Portal;

const Content = React.forwardRef<
    React.ComponentRef<typeof TooltipPrimitive.Content>,
    React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
    <TooltipPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        data-slot="tooltip-content"
        className={cn(
            "z-50 overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2",
            className
        )}
        {...props}
    />
));
Content.displayName = TooltipPrimitive.Content.displayName;

type TTooltipProps<T extends React.ElementType> = {
    tooltip?: React.ReactNode;
    tooltipContentProps?: Omit<React.ComponentPropsWithoutRef<typeof Content>, "children">;
    tooltipProps?: Omit<React.ComponentPropsWithoutRef<typeof Provider>, "children">;
    tooltipTriggerProps?: React.ComponentPropsWithoutRef<typeof Trigger>;
} & React.ComponentProps<T>;

function withTooltip<T extends React.ElementType>(Component: T) {
    return function ExtendComponent({ tooltip, tooltipContentProps, tooltipProps, tooltipTriggerProps, ...props }: TTooltipProps<T>) {
        const [mounted, setMounted] = React.useState(false);

        React.useEffect(() => {
            setMounted(true);
        }, []);

        const componentProps = {
            ...props,
            "aria-label": props["aria-label"] ?? (!props["aria-labelledby"] && typeof tooltip === "string" ? tooltip : undefined),
        } as React.JSX.LibraryManagedAttributes<T, React.ComponentProps<T>>;
        const component = <Component {...componentProps} />;

        if (tooltip && mounted) {
            return (
                <Provider {...tooltipProps}>
                    <Root>
                        <Trigger asChild {...tooltipTriggerProps}>
                            {component}
                        </Trigger>

                        <Portal container={document.body}>
                            <Content {...tooltipContentProps}>{tooltip}</Content>
                        </Portal>
                    </Root>
                </Provider>
            );
        }

        return component;
    };
}

export default {
    DEFAULT_DURATION,
    Content,
    Provider,
    Root,
    Trigger,
    Portal,
    withTooltip,
};
