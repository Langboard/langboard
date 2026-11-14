import useProtectedImage from "@/core/hooks/useProtectedImage";
import Button from "@/components/base/Button";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export default function ProtectedPdfPreview({
    url,
    name,
    initialPage,
    pages = [],
}: {
    url?: string;
    name?: string;
    initialPage?: number;
    pages?: number[];
}) {
    const [t] = useTranslation();
    const protectedFile = useProtectedImage(url, "application/pdf");
    const [page, setPage] = useState(
        initialPage && Number.isSafeInteger(initialPage) && initialPage > 0 && initialPage <= 999999999 ? initialPage : 1
    );
    const src = protectedFile.protected ? protectedFile.src : undefined;
    const inlineSupported = navigator.pdfViewerEnabled;
    return (
        <div className="w-[calc(100vw-4rem)] max-w-6xl">
            <p role="note">{t("dashboard.PDF source pages", { pages: pages.length ? pages.join(", ") : page })}</p>
            {pages.length > 1 &&
                pages.map(
                    (target) =>
                        Number.isSafeInteger(target) &&
                        target > 0 &&
                        target <= 999999999 && (
                            <Button key={target} variant="ghost" size="sm" onClick={() => setPage(target)}>
                                {t("dashboard.Source pages", { pages: target })}
                            </Button>
                        )
                )}
            {src && inlineSupported && (
                <iframe
                    key={`${src}-${page}`}
                    title={name ?? t("dashboard.PDF document preview")}
                    src={`${src}#page=${page}`}
                    className="h-[calc(100dvh-12rem)] w-full border-0"
                />
            )}
            {!inlineSupported && <p>{t("dashboard.PDF inline unavailable")}</p>}
            {src && (
                <a href={src} download={name}>
                    {t("dashboard.Download PDF")}
                </a>
            )}
            {!src && <p role="status">{t("common.No preview")}</p>}
        </div>
    );
}
