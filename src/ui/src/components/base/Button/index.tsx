import { Slot } from "@radix-ui/react-slot";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as React from "react";
import { type VariantProps, tv } from "tailwind-variants";
import Tooltip from "@/components/base/Tooltip";
import { cn } from "@/core/utils/ComponentUtils";

export const ButtonVariants = tv(
    {
        // eslint-disable-next-line @/max-len
        base: "inline-flex items-center select-none justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
        variants: {
            isMenu: {
                true: "w-full cursor-pointer justify-start gap-2",
            },
            variant: {
                default: "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90",
                destructive: "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
                outline: "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
                secondary: "bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
                ghost: "hover:bg-accent hover:text-accent-foreground",
                "destructive-ghost": "hover:bg-destructive hover:text-destructive-foreground",
                link: "text-primary underline-offset-4 hover:underline",
            },
            size: {
                default: "h-9 px-4 py-2",
                sm: "h-8 rounded-md px-3 text-xs",
                lg: "h-10 rounded-md px-8",
                icon: "size-9",
                "icon-sm": "size-8",
            },
        },
        defaultVariants: {
            variant: "default",
            size: "default",
        },
    },
    {
        responsiveVariants: true,
    }
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof ButtonVariants> {
    asChild?: bool;
    titleSide?: React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>["side"];
    titleAlign?: React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>["align"];
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant, size, title, titleSide, titleAlign, isMenu, asChild = false, ...props }, ref) => {
        const Comp = asChild ? Slot : "button";
        const btn = <Comp data-ui-control="" className={cn(ButtonVariants({ variant, size, isMenu, className }))} ref={ref} {...props} />;
        if (!title) {
            return btn;
        } else {
            return (
                <Tooltip.Root>
                    <Tooltip.Trigger asChild>{btn}</Tooltip.Trigger>
                    <Tooltip.Content side={titleSide} align={titleAlign}>
                        {title}
                    </Tooltip.Content>
                </Tooltip.Root>
            );
        }
    }
);
Button.displayName = "Button";

export default Button;
