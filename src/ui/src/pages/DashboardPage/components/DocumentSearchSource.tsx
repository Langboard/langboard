import { useTranslation } from "react-i18next";
import Command from "@/components/base/Command";
import type { CardDocumentMatch } from "@/controllers/api/card/useSearchCardContext";
import { ROUTES } from "@/core/routing/constants";

export function documentSourceRoute(projectUID: string, cardUID: string, source: CardDocumentMatch) {
    const params = new URLSearchParams({ attachment: source.attachment_uid });
    const pages = [source.page, ...(source.pages ?? [])].filter(
        (page): page is number => Number.isSafeInteger(page) && page! > 0 && page! <= 999999999
    );
    if (pages.length) params.set("source-pages", [...new Set(pages)].slice(0, 20).join(","));
    const cardRoute = ROUTES.BOARD.CARD(encodeURIComponent(projectUID), encodeURIComponent(cardUID));
    return `${cardRoute}?${params}#attachment-${encodeURIComponent(source.attachment_uid)}`;
}

export default function DocumentSearchSource({
    projectUID,
    cardUID,
    cardTitle,
    source,
    onSelect,
}: {
    projectUID: string;
    cardUID: string;
    cardTitle: string;
    source: CardDocumentMatch;
    onSelect: (route: string) => void;
}) {
    const [t] = useTranslation();
    const route = documentSourceRoute(projectUID, cardUID, source);
    const pages = new URLSearchParams(route.split("?")[1].split("#")[0]).get("source-pages");
    const pageLabel = pages ? t("dashboard.Source pages", { pages }) : "";
    const actionLabel = t(`dashboard.${pages ? "Go to attachment source pages" : "Go to attachment"}`);
    return (
        <Command.Item
            value={`search-source:${cardUID}:${source.attachment_uid}:${source.chunk_id ?? ""}`}
            keywords={[cardTitle, source.filename, source.snippet]}
            aria-label={`${source.filename}${pages ? ` · ${pageLabel}` : ""} · ${cardTitle}`}
            aria-description={actionLabel}
            onSelect={() => onSelect(route)}
            className="min-w-0 rounded-lg py-2.5 pl-8"
        >
            <span className="min-w-0 flex-1">
                <span className="block break-words text-xs">
                    {source.filename}
                    {pages ? ` · ${pageLabel}` : ""} · {source.snippet}
                </span>
                <span className="block text-xs text-muted-foreground">{actionLabel}</span>
            </span>
        </Command.Item>
    );
}
