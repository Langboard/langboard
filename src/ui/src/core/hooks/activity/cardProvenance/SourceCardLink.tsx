export function sourceCardPath(source: unknown): string | undefined {
    if (!source || typeof source !== "object") {
        return undefined;
    }
    const { type, uid, project_uid, url } = source as Record<string, unknown>;
    if (
        type !== "project_card" ||
        typeof uid !== "string" ||
        typeof project_uid !== "string" ||
        !/^[A-Za-z0-9_-]+$/.test(uid) ||
        !/^[A-Za-z0-9_-]+$/.test(project_uid)
    ) {
        return undefined;
    }
    const path = `/board/${project_uid}/${uid}`;
    return url === path ? path : undefined;
}

export default function SourceCardLink({ source, label, navigate }: { source: unknown; label: string; navigate: (path: string) => void }) {
    const path = sourceCardPath(source);
    if (!path) {
        return <span>{label}</span>;
    }
    return (
        <a
            href={path}
            className="link"
            onClick={(event) => {
                if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
                    event.preventDefault();
                    navigate(path);
                }
            }}
        >
            {label}
        </a>
    );
}
