import * as React from "react";
import { type VariantProps, tv } from "tailwind-variants";
import { cn } from "@/core/utils/ComponentUtils";

export const BadgeVariants = tv(
    {
        // eslint-disable-next-line @/max-len
        base: "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 select-none",
        variants: {
            variant: {
                default: "border-transparent bg-muted text-foreground hover:bg-accent",
                secondary: "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
                destructive: "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80",
                success: "border-transparent bg-green-600 text-white hover:bg-green-600/80",
                outline: "text-foreground",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    },
    {
        responsiveVariants: true,
    }
);

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof BadgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
    return <div className={cn(BadgeVariants({ variant }), className)} {...props} />;
}

export default Badge;
