import * as React from "react";
import { cn } from "@/core/utils/ComponentUtils";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
    resize?: "none" | "vertical" | "horizontal" | "both";
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(({ className, resize = "vertical", ...props }, ref) => {
    const resizeMap = {
        none: "resize-none",
        vertical: "resize-y",
        horizontal: "resize-x",
        both: "resize",
    };

    return (
        <textarea
            className={cn(
                // eslint-disable-next-line @/max-len
                "flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm leading-6 ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:opacity-50",
                resizeMap[resize],
                className
            )}
            ref={ref}
            {...props}
        />
    );
});
Textarea.displayName = "Textarea";

export default Textarea;
