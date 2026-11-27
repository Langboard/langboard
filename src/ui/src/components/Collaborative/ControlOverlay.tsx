import CollaborativeUserLabel from "@/components/Collaborative/UserLabel";

export interface ICollaborativeControlOverlayProps {
    className?: string;
    color: string;
    labelClassName?: string;
    labelStyle?: React.CSSProperties;
    name: string;
    title?: string;
}

function CollaborativeControlOverlay({ className, color, labelClassName, labelStyle, name, title }: ICollaborativeControlOverlayProps) {
    return (
        <>
            <span
                className={className ?? "pointer-events-none absolute inset-0 z-[9998] rounded-md border"}
                style={{
                    borderColor: color,
                    boxShadow: `0 0 0 1px ${color}`,
                }}
            />
            <CollaborativeUserLabel className={labelClassName} color={color} name={name} style={labelStyle} title={title} />
        </>
    );
}

export default CollaborativeControlOverlay;
