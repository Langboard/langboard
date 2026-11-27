import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";

export interface ICollaborativeUserLabelProps extends Omit<React.ComponentPropsWithoutRef<"span">, "color"> {
    className?: string;
    color: string;
    name: string;
}

function CollaborativeUserLabel({ className, color, name, style, ...props }: ICollaborativeUserLabelProps) {
    return (
        <span
            {...props}
            className={cn("pointer-events-none rounded px-1.5 py-0.5 text-[10px] font-medium leading-none shadow-sm", className)}
            style={{
                backgroundColor: color,
                color: Utils.Color.getTextColorFromHex(color),
                ...style,
            }}
        >
            {name}
        </span>
    );
}

export default CollaborativeUserLabel;
