import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import CloudUpload from "./CloudUpload";
import CloudDocument from "./CloudDocument";
import useAuthStore from "@/core/stores/AuthStore";
import { Tree } from "react-arborist";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import CachedImage from "@/components/CachedImage";
import ImagePreviewDialog from "@/components/ImagePreviewDialog";
import useDownloadFile from "@/core/hooks/useDownloadFile";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import useBoardCloud, { CloudItem } from "@/controllers/api/board/cloud/useBoardCloud";

type CloudNode = { id: string; name: string; item?: CloudItem; children?: CloudNode[] };
const categories = ["recent", "files", "links", "media", "all"] as const;
const folderNames = ["Inbox", "Documents", "Media"] as const;

export default function BoardCloud({ projectUID, panel = false }: { projectUID: string; panel?: boolean }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const [category, setCategory] = useState<(typeof categories)[number]>("all");
    const [layout, setLayout] = useState<"list" | "grid">("list");
    const [folder, setFolder] = useState<string>();
    const [preview, setPreview] = useState<CloudItem>();
    const [document, setDocument] = useState<{ item: CloudItem; scope: string }>();
    const query = useBoardCloud(projectUID, category);
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const scope = `${projectUID}:${actor}:${identity}`;
    // A failed authorization refresh must not leave previously visible files on screen.
    const items = useMemo(() => {
        if (query.isError) return [];
        return [...new Map(query.data?.pages.flatMap((page) => page.items).map((item) => [item.id, item])).values()];
    }, [query.data, query.isError, category]);
    const mixed = (category === "all" || category === "recent") && query.data?.pages[0]?.capabilities.all_recent === "attachments_and_links";
    const visible = items.filter((item) => !folder || item.folder_id === folder);
    const tree: CloudNode[] = (category === "links" ? (["Links"] as const) : mixed ? ([...folderNames, "Links"] as const) : folderNames).map(
        (name) => ({
            id: name.toLowerCase(),
            name: t(`cloud.${name}`),
            children: items.filter((item) => item.folder_id === name.toLowerCase()).map((item) => ({ id: item.id, name: item.name, item })),
        })
    );
    const limitations = query.isError ? [] : (query.data?.pages.flatMap((page) => page.limitations ?? []) ?? []);
    const openItem = (item: CloudItem) => {
        if (item.kind === "link") window.open(item.url, "_blank", "noopener,noreferrer");
        else setPreview(item);
    };
    const openSource = (item: Pick<CloudItem, "source">) =>
        navigate(
            item.source.kind === "project"
                ? ROUTES.BOARD.CLOUD(projectUID)
                : item.source.card_uid
                  ? ROUTES.BOARD.CARD(projectUID, item.source.card_uid)
                  : ROUTES.BOARD.WIKI_PAGE(projectUID, item.source.wiki_uid!)
        );
    return (
        <section aria-label={t("cloud.Cloud")} data-workbench-context="" className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
            <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
                <h1 className="text-sm font-semibold">{t("cloud.Cloud")}</h1>
                {panel && (
                    <Button variant="ghost" size="sm" onClick={() => navigate(ROUTES.BOARD.CLOUD(projectUID))}>
                        {t("cloud.Open cloud")}
                    </Button>
                )}
            </header>
            <div className="flex shrink-0 flex-wrap gap-1 border-b p-2" aria-label={t("cloud.Categories")}>
                {categories.map((view) => (
                    <Button
                        key={view}
                        size="sm"
                        variant={category === view ? "secondary" : "ghost"}
                        aria-pressed={category === view}
                        onClick={() => {
                            setCategory(view);
                            setFolder(undefined);
                            setPreview(undefined);
                        }}
                    >
                        {t(`cloud.${view}`)}
                    </Button>
                ))}
            </div>
            <CloudUpload
                key={`${projectUID}:${actor}:${identity}`}
                projectUID={projectUID}
                folder={folder === "documents" || folder === "media" ? folder : "inbox"}
                enabled={!query.isError && query.data?.pages[0]?.capabilities.upload === true}
            />
            <p className="shrink-0 px-3 py-2 text-xs text-muted-foreground">
                {t(category === "links" ? "cloud.Read only links" : mixed ? "cloud.Read mixed sources" : "cloud.Read only attachments")}
            </p>
            <>
                <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
                    <nav aria-label={t("cloud.Folders")} className="shrink-0 border-b p-2">
                        <Button size="sm" variant="ghost" aria-pressed={!folder} onClick={() => setFolder(undefined)}>
                            {t("cloud.All folders")}
                        </Button>
                        <Tree<CloudNode>
                            data={tree}
                            width="100%"
                            height={panel ? 160 : 200}
                            rowHeight={30}
                            indent={16}
                            openByDefault={false}
                            disableDrag
                            disableDrop
                            disableEdit
                            disableMultiSelection
                            onActivate={(node) => {
                                if (node.data.item) openItem(node.data.item);
                                else {
                                    setFolder(node.id);
                                    node.toggle();
                                }
                            }}
                        >
                            {({ node, style, dragHandle }) => (
                                <div ref={dragHandle} style={style} className="flex items-center gap-2 text-sm">
                                    <button
                                        type="button"
                                        className="flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1 text-left hover:bg-muted"
                                        aria-pressed={!node.data.item && folder === node.id}
                                        title={node.data.name}
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            if (node.data.item) openItem(node.data.item);
                                            else {
                                                setFolder(node.id);
                                                node.toggle();
                                            }
                                        }}
                                    >
                                        <IconComponent
                                            icon={
                                                node.data.item
                                                    ? node.data.item.kind === "link"
                                                        ? "link"
                                                        : "file"
                                                    : node.isOpen
                                                      ? "folder-open"
                                                      : "folder"
                                            }
                                            size="4"
                                        />
                                        <span className="truncate">{node.data.name}</span>
                                    </button>
                                </div>
                            )}
                        </Tree>
                    </nav>
                    <div className="flex items-center justify-between gap-2 p-2">
                        <span className="text-xs text-muted-foreground">
                            {t(category === "links" ? "cloud.Loaded links" : mixed ? "cloud.Loaded items" : "cloud.Loaded files", {
                                count: visible.length,
                            })}
                        </span>
                        <div className="flex gap-1" aria-label={t("cloud.View")}>
                            {(["list", "grid"] as const).map((view) => (
                                <Button key={view} size="sm" variant="ghost" aria-pressed={layout === view} onClick={() => setLayout(view)}>
                                    {t(`cloud.${view}`)}
                                </Button>
                            ))}
                        </div>
                    </div>
                    {query.isPending && (
                        <p role="status" className="p-3 text-sm">
                            {t("cloud.Loading")}
                        </p>
                    )}
                    {query.isError && (
                        <div role="alert" className="space-y-2 p-3 text-sm">
                            <p>{t("cloud.Load failed")}</p>
                            <Button size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}>
                                {t("cloud.Retry")}
                            </Button>
                        </div>
                    )}
                    {!query.isPending && !query.isError && !visible.length && (
                        <p role="status" className="p-3 text-sm">
                            {t(
                                category === "links"
                                    ? query.hasNextPage
                                        ? "cloud.No links in loaded pages"
                                        : "cloud.No links"
                                    : query.hasNextPage
                                      ? "cloud.No files in loaded pages"
                                      : "cloud.No files"
                            )}
                        </p>
                    )}
                    {limitations.length > 0 && (
                        <div role="status" className="space-y-1 p-3 text-sm">
                            <p>{t("cloud.Source scan limitations", { count: limitations.length })}</p>
                            {limitations.map((entry, index) => (
                                <Button key={`${entry.source.url}-${index}`} size="sm" variant="ghost" onClick={() => openSource(entry)}>
                                    {t("cloud.Source")}: {entry.source.title}
                                </Button>
                            ))}
                        </div>
                    )}
                    <CloudCollection
                        key={`${scope}:${category}:${folder ?? "all"}:${layout}`}
                        items={visible}
                        layout={layout}
                        label={t(category === "links" ? "cloud.Links" : mixed ? "cloud.Cloud items" : "cloud.Attachments")}
                        onPreview={setPreview}
                        onSource={openSource}
                        onDocument={(item) => setDocument({ item, scope })}
                    />
                    {query.hasNextPage && !query.isError && (
                        <div className="p-3">
                            <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>
                                {t(query.isFetchingNextPage ? "cloud.Loading" : "cloud.Load more")}
                            </Button>
                        </div>
                    )}
                </div>
            </>
            {document &&
                document.scope === scope &&
                !query.isError &&
                items.some((item) => item.id === document.item.id && item.url === document.item.url) && (
                    <CloudDocument
                        key={`${scope}:${document.item.id}:${document.item.url}`}
                        projectUID={projectUID}
                        item={document.item}
                        onClose={() => setDocument(undefined)}
                    />
                )}
            {preview && items.includes(preview) && !query.isError && (
                <ImagePreviewDialog
                    files={[{ name: preview.name, url: preview.url, type: preview.media_type }]}
                    initialIndex={0}
                    onClose={() => setPreview(undefined)}
                />
            )}
        </section>
    );
}

function CloudCollection({
    items,
    layout,
    label,
    onPreview,
    onSource,
    onDocument,
}: {
    items: CloudItem[];
    layout: "list" | "grid";
    label: string;
    onPreview: (item: CloudItem) => void;
    onSource: (item: CloudItem) => void;
    onDocument: (item: CloudItem) => void;
}) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const [columns, setColumns] = useState(1);
    const [focusedRow, setFocusedRow] = useState<number>();
    useLayoutEffect(() => {
        const element = scrollRef.current;
        if (!element) return;
        const measure = () => setColumns(layout === "grid" ? Math.max(1, Math.floor((element.clientWidth - 8) / 168)) : 1);
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [layout]);
    const rowCount = Math.ceil(items.length / columns);
    const virtualizer = useVirtualizer({
        count: rowCount,
        getScrollElement: () => scrollRef.current,
        estimateSize: () => (layout === "grid" ? 240 : 140),
        getItemKey: (index) => `${columns}:${items[index * columns].id}`,
        overscan: 3,
        gap: 8,
        rangeExtractor: (range) => {
            const indexes = defaultRangeExtractor(range);
            // Keep the focused row and its neighbors mounted so Tab can cross a virtual boundary.
            if (focusedRow !== undefined) {
                for (let index = Math.max(0, focusedRow - 1); index <= Math.min(rowCount - 1, focusedRow + 1); index++) indexes.push(index);
            }
            return [...new Set(indexes)].sort((a, b) => a - b);
        },
    });
    useLayoutEffect(() => {
        virtualizer.measure();
    }, [columns, virtualizer]);
    return (
        <div
            ref={scrollRef}
            className="min-h-40 flex-1 overflow-auto p-2"
            onFocusCapture={(event) => {
                const row = event.target.closest<HTMLElement>("[data-cloud-row]");
                if (row) setFocusedRow(Number(row.dataset.cloudRow));
            }}
            onBlurCapture={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setFocusedRow(undefined);
            }}
        >
            <ul aria-label={label} data-layout={layout} className="relative" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((row) => (
                    <li
                        key={row.key}
                        role="presentation"
                        data-index={row.index}
                        data-cloud-row={row.index}
                        ref={virtualizer.measureElement}
                        className="absolute left-0 top-0 w-full"
                        style={{ transform: `translateY(${row.start}px)` }}
                    >
                        <ul role="presentation" className="grid gap-2" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
                            {items
                                .slice(row.index * columns, (row.index + 1) * columns)
                                .map((item) =>
                                    item.kind === "link" ? (
                                        <CloudLink key={item.id} item={item} onSource={() => onSource(item)} />
                                    ) : (
                                        <CloudFile
                                            key={item.id}
                                            item={item}
                                            grid={layout === "grid"}
                                            onPreview={() => onPreview(item)}
                                            onSource={() => onSource(item)}
                                            onDocument={
                                                item.attachment_uid &&
                                                (item.source.kind === "project" || (item.source.kind === "wiki" && item.source.wiki_uid))
                                                    ? () => onDocument(item)
                                                    : undefined
                                            }
                                        />
                                    )
                                )}
                        </ul>
                    </li>
                ))}
            </ul>
        </div>
    );
}

function CloudFile({
    item,
    grid,
    onPreview,
    onSource,
    onDocument,
}: {
    item: CloudItem;
    grid: boolean;
    onPreview: () => void;
    onSource: () => void;
    onDocument?: () => void;
}) {
    const [t] = useTranslation();
    const [failed, setFailed] = useState(false);
    const { download, isDownloading } = useDownloadFile({ url: item.url, filename: item.name, onError: () => setFailed(true) });
    return (
        <li role="listitem" className="min-w-0 rounded border p-2">
            {grid && item.media_type.startsWith("image/") && (
                <button type="button" className="w-full" onClick={onPreview} aria-label={item.name}>
                    <CachedImage src={item.url} alt={item.name} className="h-24 w-full object-contain" />
                </button>
            )}
            <button
                type="button"
                onClick={onPreview}
                className="block w-full truncate text-left text-sm font-medium hover:underline"
                title={item.name}
            >
                {item.name}
            </button>
            <button
                type="button"
                onClick={onSource}
                className="block w-full truncate text-left text-xs text-primary hover:underline"
                title={item.source.title}
            >
                {t("cloud.Source")}: {item.source.title}
            </button>
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString()}</time>
                <Button
                    size="sm"
                    variant="ghost"
                    disabled={isDownloading}
                    onClick={() => {
                        setFailed(false);
                        download();
                    }}
                >
                    {t("common.Download")}
                </Button>
            </div>
            {onDocument && (
                <Button size="sm" variant="outline" onClick={onDocument}>
                    {t("cloud.Document")}
                </Button>
            )}
            {failed && (
                <p role="alert" className="text-xs text-destructive">
                    {t("errors.Download failed.")}
                </p>
            )}
        </li>
    );
}

function CloudLink({ item, onSource }: { item: CloudItem; onSource: () => void }) {
    const [t] = useTranslation();
    return (
        <li role="listitem" className="min-w-0 rounded border p-2">
            <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block truncate text-sm font-medium hover:underline"
                title={item.url}
            >
                {item.name}
            </a>
            <p className="truncate text-xs text-muted-foreground">{item.url}</p>
            <button type="button" onClick={onSource} className="block w-full truncate text-left text-xs text-primary hover:underline">
                {t("cloud.Source")}: {item.source.title}
            </button>
        </li>
    );
}
