"use client";

import * as ProgressPrimitive from "@radix-ui/react-progress";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/core/utils/ComponentUtils";
import { type TSize, DimensionMap } from "@/core/utils/VariantUtils";

interface ProgressProps extends React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root> {
    indeterminate?: bool;
    height?: TSize;
    indicatorClassName?: string;
}

const Progress = React.forwardRef<React.ComponentRef<typeof ProgressPrimitive.Root>, ProgressProps>(
    ({ className, value, indeterminate = false, height = "4", indicatorClassName, ...props }, ref) => {
        const [t] = useTranslation();
        return (
            <ProgressPrimitive.Root
                ref={ref}
                aria-label={t("common.Loading...")}
                value={indeterminate ? null : value}
                className={cn("relative w-full overflow-hidden rounded-full bg-secondary", DimensionMap.height[height], className)}
                {...props}
            >
                <ProgressPrimitive.Indicator
                    className={cn(
                        "h-full w-full flex-1 bg-primary transition-all",
                        indeterminate && "origin-left animate-progress",
                        indicatorClassName
                    )}
                    style={{ transform: `translateX(-${100 - (value || 0)}%)` }}
                />
            </ProgressPrimitive.Root>
        );
    }
);
Progress.displayName = ProgressPrimitive.Root.displayName;

export default Progress;
