import { useTranslation } from "react-i18next";
import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router";

// Resolve only an attachment already loaded by the authorized owning card.
export default function AttachmentSearchTarget({ attachmentUID, children }: { attachmentUID: string; children: ReactNode }) {
    const [t] = useTranslation();
    const location = useLocation();
    const params = new URLSearchParams(location.search);
    const targeted = params.get("attachment") === attachmentUID;
    const pages = params
        .get("source-pages")
        ?.split(",")
        .filter((page) => /^[1-9]\d{0,8}$/.test(page))
        .slice(0, 20)
        .join(", ");
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!targeted) return;
        ref.current?.scrollIntoView({ block: "center" });
        ref.current?.focus({ preventScroll: true });
    }, [targeted, location.key]);
    return (
        <div
            ref={ref}
            id={`attachment-${encodeURIComponent(attachmentUID)}`}
            tabIndex={targeted ? -1 : undefined}
            className={targeted ? "rounded ring-2 ring-ring" : undefined}
        >
            {targeted && pages && (
                <p role="note" className="text-xs text-muted-foreground">
                    {t("dashboard.Search attachment source limitation", { pages })}
                </p>
            )}
            {children}
        </div>
    );
}
