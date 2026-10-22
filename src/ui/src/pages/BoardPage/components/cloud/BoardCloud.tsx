import { useMemo, useState } from "react";
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
    const query = useBoardCloud(projectUID, category);
    // A failed authorization refresh must not leave previously visible files on screen.
    const items = useMemo(() => {
        if (query.isError) return [];
        return [...new Map(query.data?.pages.flatMap((page) => page.items).map((item) => [item.id, item])).values()];
    }, [query.data, query.isError, category]);
    const visible = items.filter((item) => !folder || item.folder_id === folder);
    const tree: CloudNode[] = (category === "links" ? (["Links"] as const) : folderNames).map((name) => ({
        id: name.toLowerCase(),
        name: t(`cloud.${name}`),
        children: items.filter((item) => item.folder_id === name.toLowerCase()).map((item) => ({ id: item.id, name: item.name, item })),
    }));
    const limitations = query.isError ? [] : (query.data?.pages.flatMap((page) => page.limitations ?? []) ?? []);
    const openItem = (item: CloudItem) => {
        if (item.kind === "link") window.open(item.url, "_blank", "noopener,noreferrer");
        else setPreview(item);
    };
    const openSource = (item: Pick<CloudItem, "source">) =>
        navigate(
            item.source.card_uid ? ROUTES.BOARD.CARD(projectUID, item.source.card_uid) : ROUTES.BOARD.WIKI_PAGE(projectUID, item.source.wiki_uid!)
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
            <p className="shrink-0 px-3 py-2 text-xs text-muted-foreground">
                {t(category === "links" ? "cloud.Read only links" : "cloud.Read only attachments")}
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
                            {t(category === "links" ? "cloud.Loaded links" : "cloud.Loaded files", { count: visible.length })}
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
                    <ul
                        aria-label={t(category === "links" ? "cloud.Links" : "cloud.Attachments")}
                        data-layout={layout}
                        className={layout === "grid" ? "grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-2 p-2" : "space-y-1 p-2"}
                    >
                        {visible.map((item) =>
                            item.kind === "link" ? (
                                <CloudLink key={item.id} item={item} onSource={() => openSource(item)} />
                            ) : (
                                <CloudFile
                                    key={item.id}
                                    item={item}
                                    grid={layout === "grid"}
                                    onPreview={() => setPreview(item)}
                                    onSource={() => openSource(item)}
                                />
                            )
                        )}
                    </ul>
                    {query.hasNextPage && !query.isError && (
                        <div className="p-3">
                            <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>
                                {t(query.isFetchingNextPage ? "cloud.Loading" : "cloud.Load more")}
                            </Button>
                        </div>
                    )}
                </div>
            </>
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

function CloudFile({ item, grid, onPreview, onSource }: { item: CloudItem; grid: boolean; onPreview: () => void; onSource: () => void }) {
    const [t] = useTranslation();
    const [failed, setFailed] = useState(false);
    const { download, isDownloading } = useDownloadFile({ url: item.url, filename: item.name, onError: () => setFailed(true) });
    return (
        <li className="min-w-0 rounded border p-2">
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
        <li className="min-w-0 rounded border p-2">
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
