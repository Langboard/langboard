import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { createPortal } from "react-dom";
import ImagePreviewDialog from "@/components/ImagePreviewDialog";
import { protectedImagePath } from "@/core/hooks/useProtectedImage";
import useAuthStore, { getAuthStore } from "@/core/stores/AuthStore";

export function sourcePages(search: string): number[] {
    const values = new URLSearchParams(search).getAll("source-pages");
    if (values.length !== 1 || !/^[1-9]\d{0,8}(,[1-9]\d{0,8}){0,19}$/.test(values[0])) return [];
    return [...new Set(values[0].split(",").map(Number))];
}

// Resolve only an attachment already loaded by the authorized owning card.
export default function AttachmentSearchTarget({
    attachmentUID,
    file,
    children,
}: {
    attachmentUID: string;
    file?: { name: string; url: string };
    children: ReactNode;
}) {
    const [t] = useTranslation();
    const location = useLocation();
    const params = new URLSearchParams(location.search);
    const targeted = params.getAll("attachment").length === 1 && params.get("attachment") === attachmentUID;
    const pages = sourcePages(location.search);
    const pdf = !!file && /\.pdf$/i.test(file.name) && !!protectedImagePath(file.url);
    const session = useAuthStore((store) => store.getSessionVersion());
    const navigationSession = useMemo(() => getAuthStore().getSessionVersion(), [location.key]);
    const [opened, setOpened] = useState<{ key: string; session: number } | null>(null);
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!targeted) return;
        if (pdf && pages.length) setOpened({ key: location.key, session: navigationSession });
        ref.current?.scrollIntoView({ block: "center" });
        ref.current?.focus({ preventScroll: true });
    }, [targeted, location.key, pdf, navigationSession]);
    return (
        <div
            ref={ref}
            id={`attachment-${encodeURIComponent(attachmentUID)}`}
            tabIndex={targeted ? -1 : undefined}
            className={targeted ? "rounded ring-2 ring-ring" : undefined}
        >
            {targeted && pages.length > 0 && !pdf && (
                <p role="note" className="text-xs text-muted-foreground">
                    {t("dashboard.Search attachment source limitation", { pages: pages.join(", ") })}
                </p>
            )}
            {children}
            {file &&
                opened?.key === location.key &&
                opened.session === session &&
                targeted &&
                pdf &&
                createPortal(
                    <ImagePreviewDialog
                        files={[{ ...file, type: "application/pdf" }]}
                        initialIndex={0}
                        initialPage={pages[0]}
                        sourcePages={pages}
                        onClose={() => setOpened(null)}
                    />,
                    document.body
                )}
        </div>
    );
}
