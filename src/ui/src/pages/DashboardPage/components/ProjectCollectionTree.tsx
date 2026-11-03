import { useId, useMemo, useState } from "react";
import { Tree } from "react-arborist";
import { useDragDropManager } from "react-dnd";
import { useTranslation } from "react-i18next";
import SharedDndProvider from "@/components/SharedDndProvider";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import Popover from "@/components/base/Popover";
import { Project } from "@/core/models";
import useToggleStarProject from "@/controllers/api/dashboard/useToggleStarProject";
import type { ProjectCollection } from "./useProjectCollections";

import { BOARD_REFERENCE_DRAG_TYPE } from "./BoardReferenceDrag";
const COLLECTION_BOARD_DRAG_TYPE = "application/x-langboard-collection-board";
interface CollectionNode {
    id: string;
    name: string;
    collectionUID: string;
    boardUID?: string;
    children?: CollectionNode[];
}
interface Props {
    collections: ProjectCollection[];
    projects: Project.TModel[];
    visibleCollectionUIDs?: string[];
    busy: boolean;
    save: (items: ProjectCollection[]) => Promise<unknown>;
    navigate: (uid: string) => void;
}
export default function ProjectCollectionTree(props: Props) {
    return (
        <SharedDndProvider>
            <CollectionTree {...props} />
        </SharedDndProvider>
    );
}
function CollectionTree({ collections, projects, busy, save, navigate, visibleCollectionUIDs }: Props) {
    const [t] = useTranslation();
    const manager = useDragDropManager();
    const treeDragType = `application/x-langboard-collection-tree-${useId()
        .replace(/[^a-z0-9]/gi, "")
        .toLowerCase()}`;
    const [failed, setFailed] = useState(false);
    const projectMap = useMemo(() => new Map(projects.map((project) => [project.uid, project])), [projects]);
    const data = useMemo(
        () =>
            collections
                .filter((item) => !visibleCollectionUIDs || visibleCollectionUIDs.includes(item.uid))
                .map(
                    (collection): CollectionNode => ({
                        id: collection.uid,
                        name: collection.name,
                        collectionUID: collection.uid,
                        children: collection.board_uids.map((uid) => ({
                            id: `${collection.uid}:${uid}`,
                            name: projectMap.get(uid)?.title ?? t("dashboard.Board unavailable"),
                            collectionUID: collection.uid,
                            boardUID: uid,
                        })),
                    })
                ),
        [collections, projectMap, t, visibleCollectionUIDs]
    );
    const persist = async (items: ProjectCollection[]) => {
        setFailed(false);
        try {
            await save(items);
        } catch {
            setFailed(true);
        }
    };
    const sort = (uid: string) =>
        void persist(
            collections.map((item) =>
                item.uid === uid
                    ? {
                          ...item,
                          board_uids: [...item.board_uids].sort((a, b) =>
                              (projectMap.get(a)?.title ?? "").localeCompare(projectMap.get(b)?.title ?? "")
                          ),
                      }
                    : item
            )
        );
    const shift = (uid: string, offset: number) => {
        const index = collections.findIndex((item) => item.uid === uid);
        if (index < 0 || index + offset < 0 || index + offset >= collections.length) return;
        const next = [...collections];
        [next[index], next[index + offset]] = [next[index + offset], next[index]];
        void persist(next);
    };
    const moveBoard = (sourceUID: string, boardUID: string, targetUID: string, index: number) => {
        if (busy || !projectMap.has(boardUID)) return;
        const source = collections.find((item) => item.uid === sourceUID);
        const target = collections.find((item) => item.uid === targetUID);
        if (!source || !target || (source !== target && target.board_uids.includes(boardUID))) return;
        const from = source.board_uids.indexOf(boardUID);
        if (from < 0 || index < 0 || index > target.board_uids.length) return;
        const destination = index - (source === target && from < index ? 1 : 0);
        if (source === target && from === destination) return;
        const next = collections.map((item) => ({
            ...item,
            board_uids: item.board_uids.filter((uid) => item.uid !== sourceUID || uid !== boardUID),
        }));
        next.find((item) => item.uid === targetUID)!.board_uids.splice(destination, 0, boardUID);
        void persist(next);
    };
    return (
        <div
            onDragOverCapture={(event) => {
                if (!event.dataTransfer.types.includes(COLLECTION_BOARD_DRAG_TYPE) || event.dataTransfer.types.includes(treeDragType)) return;
                // Arborist owns ordering inside one tree; its drag state is not shared between trees.
                event.stopPropagation();
                if (!busy && (event.target as HTMLElement).closest("[data-collection-uid]")) {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                }
            }}
            onDropCapture={(event) => {
                if (!event.dataTransfer.types.includes(COLLECTION_BOARD_DRAG_TYPE) || event.dataTransfer.types.includes(treeDragType)) return;
                event.preventDefault();
                event.stopPropagation();
                if (busy) return;
                const targetUID = (event.target as HTMLElement).closest<HTMLElement>("[data-collection-uid]")?.dataset.collectionUid;
                const target = collections.find((item) => item.uid === targetUID);
                const sourceID = event.dataTransfer.getData(COLLECTION_BOARD_DRAG_TYPE);
                const source = collections.find((item) => item.board_uids.some((uid) => `${item.uid}:${uid}` === sourceID));
                const boardUID = source?.board_uids.find((uid) => `${source.uid}:${uid}` === sourceID);
                if (source && target && source !== target && boardUID) moveBoard(source.uid, boardUID, target.uid, target.board_uids.length);
            }}
            onDragOver={(event) => {
                if (event.dataTransfer.types.includes(COLLECTION_BOARD_DRAG_TYPE)) return;
                if (!busy && event.dataTransfer.types.includes(BOARD_REFERENCE_DRAG_TYPE)) {
                    event.preventDefault();
                    event.stopPropagation();
                    event.dataTransfer.dropEffect = "copy";
                }
            }}
            onDrop={(event) => {
                if (event.dataTransfer.types.includes(COLLECTION_BOARD_DRAG_TYPE)) return;
                if (busy || !event.dataTransfer.types.includes(BOARD_REFERENCE_DRAG_TYPE)) return;
                const target = (event.target as HTMLElement).closest<HTMLElement>("[data-collection-uid]")?.dataset.collectionUid;
                const uid = event.dataTransfer.getData(BOARD_REFERENCE_DRAG_TYPE);
                const collection = collections.find((item) => item.uid === target);
                if (!collection || !projectMap.has(uid)) return;
                event.preventDefault();
                event.stopPropagation();
                if (!collection.board_uids.includes(uid))
                    void persist(collections.map((item) => (item.uid === target ? { ...item, board_uids: [...item.board_uids, uid] } : item)));
            }}
        >
            {failed && (
                <p role="alert" className="px-2 text-xs text-destructive">
                    {t("dashboard.Could not save collection")}
                </p>
            )}
            <Tree<CollectionNode>
                data={data}
                width="100%"
                height={Math.min(320, Math.max(64, (data.length + data.reduce((sum, item) => sum + (item.children?.length ?? 0), 0)) * 32))}
                rowHeight={32}
                indent={12}
                openByDefault={false}
                dndManager={manager}
                disableMultiSelection
                disableEdit
                disableDrag={(node) => busy || (!!visibleCollectionUIDs && !node.boardUID) || (!!node.boardUID && !projectMap.has(node.boardUID))}
                disableDrop={({ parentNode, dragNodes }) =>
                    busy ||
                    (dragNodes.some((node) => !node.data.boardUID)
                        ? !!parentNode && !parentNode.isRoot
                        : !parentNode || parentNode.isRoot || !!parentNode.data.boardUID)
                }
                onActivate={(node) => {
                    if (node.data.boardUID) {
                        if (projectMap.has(node.data.boardUID)) navigate(node.data.boardUID);
                    } else node.toggle();
                }}
                onMove={({ dragIds, parentId, index }) => {
                    if (busy || dragIds.length !== 1) return;
                    const dragged = data.flatMap((item) => [item, ...(item.children ?? [])]).find((item) => item.id === dragIds[0]);
                    if (!dragged) return;
                    if (!dragged.boardUID && !parentId && !visibleCollectionUIDs) {
                        const from = collections.findIndex((item) => item.uid === dragged.collectionUID);
                        const next = collections.filter((item) => item.uid !== dragged.collectionUID);
                        next.splice(index - (from < index ? 1 : 0), 0, collections[from]);
                        void persist(next);
                    } else if (dragged.boardUID && parentId) {
                        moveBoard(dragged.collectionUID, dragged.boardUID, parentId, index);
                    }
                }}
            >
                {({ node, style, dragHandle }) => (
                    <div
                        ref={dragHandle}
                        style={style}
                        data-collection-uid={node.data.collectionUID}
                        className="flex min-w-0 items-center gap-1"
                        onDragStartCapture={(event) => {
                            if (!busy && node.data.boardUID && projectMap.has(node.data.boardUID)) {
                                event.dataTransfer.setData(BOARD_REFERENCE_DRAG_TYPE, node.data.boardUID);
                                event.dataTransfer.setData(COLLECTION_BOARD_DRAG_TYPE, node.data.id);
                                event.dataTransfer.setData(treeDragType, "");
                            }
                        }}
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => {
                            if (
                                event.target === event.currentTarget.firstElementChild &&
                                ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)
                            ) {
                                node.focus();
                            } else event.stopPropagation();
                        }}
                    >
                        <Button
                            type="button"
                            variant="ghost"
                            className="h-8 min-w-0 flex-1 justify-start gap-1 px-1"
                            onClick={(event) => {
                                event.stopPropagation();
                                node.handleClick(event);
                            }}
                        >
                            <IconComponent icon={node.isLeaf ? "folder-kanban" : node.isOpen ? "chevron-down" : "chevron-right"} size="3" />
                            <span className="truncate">{node.data.name}</span>
                        </Button>
                        {node.data.boardUID && projectMap.has(node.data.boardUID) ? (
                            <>
                                <BoardFavorite project={projectMap.get(node.data.boardUID)!} />
                                <Popover.Root>
                                    <Popover.Trigger asChild>
                                        <Button
                                            type="button"
                                            size="icon"
                                            variant="ghost"
                                            className="size-6"
                                            aria-label={t("dashboard.Board actions")}
                                            disabled={busy}
                                        >
                                            <IconComponent icon="ellipsis" size="3" />
                                        </Button>
                                    </Popover.Trigger>
                                    <Popover.Content className="w-60 space-y-1 p-2">
                                        {[-1, 1].map((offset) => {
                                            const source = collections.find((item) => item.uid === node.data.collectionUID)!;
                                            const index = source.board_uids.indexOf(node.data.boardUID!);
                                            return (
                                                <Button
                                                    key={offset}
                                                    type="button"
                                                    variant="ghost"
                                                    disabled={busy || index + offset < 0 || index + offset >= source.board_uids.length}
                                                    onClick={() =>
                                                        moveBoard(source.uid, node.data.boardUID!, source.uid, index + (offset < 0 ? -1 : 2))
                                                    }
                                                >
                                                    {t(offset < 0 ? "dashboard.Move board up" : "dashboard.Move board down")}
                                                </Button>
                                            );
                                        })}
                                        <div className="max-h-48 overflow-y-auto">
                                            {collections
                                                .filter((item) => item.uid !== node.data.collectionUID)
                                                .map((target) => (
                                                    <Button
                                                        key={target.uid}
                                                        type="button"
                                                        variant="ghost"
                                                        className="w-full justify-start"
                                                        disabled={busy || target.board_uids.includes(node.data.boardUID!)}
                                                        onClick={() =>
                                                            moveBoard(
                                                                node.data.collectionUID,
                                                                node.data.boardUID!,
                                                                target.uid,
                                                                target.board_uids.length
                                                            )
                                                        }
                                                    >
                                                        <span className="truncate">
                                                            {t("dashboard.Move to collection {{name}}", { name: target.name })}
                                                        </span>
                                                    </Button>
                                                ))}
                                        </div>
                                    </Popover.Content>
                                </Popover.Root>
                            </>
                        ) : (
                            !node.data.boardUID && (
                                <>
                                    <Button
                                        type="button"
                                        size="icon"
                                        variant="ghost"
                                        className="size-6"
                                        aria-label={t("dashboard.Favorite collection")}
                                        aria-pressed={!!collections.find((item) => item.uid === node.data.collectionUID)?.starred}
                                        disabled={busy}
                                        onClick={() =>
                                            void persist(
                                                collections.map((item) =>
                                                    item.uid === node.data.collectionUID ? { ...item, starred: !item.starred } : item
                                                )
                                            )
                                        }
                                    >
                                        <IconComponent
                                            icon="star"
                                            size="3"
                                            className={
                                                collections.find((item) => item.uid === node.data.collectionUID)?.starred ? "fill-current" : undefined
                                            }
                                        />
                                    </Button>
                                    <Popover.Root>
                                        <Popover.Trigger asChild>
                                            <Button
                                                type="button"
                                                size="icon"
                                                variant="ghost"
                                                className="size-6"
                                                aria-label={t("dashboard.Collection actions")}
                                            >
                                                <IconComponent icon="ellipsis" size="3" />
                                            </Button>
                                        </Popover.Trigger>
                                        <Popover.Content className="w-48 space-y-1 p-2">
                                            <Button type="button" variant="ghost" disabled={busy} onClick={() => sort(node.data.collectionUID)}>
                                                {t("dashboard.Sort boards by name")}
                                            </Button>
                                            <Button type="button" variant="ghost" disabled={busy} onClick={() => shift(node.data.collectionUID, -1)}>
                                                {t("dashboard.Move collection up")}
                                            </Button>
                                            <Button type="button" variant="ghost" disabled={busy} onClick={() => shift(node.data.collectionUID, 1)}>
                                                {t("dashboard.Move collection down")}
                                            </Button>
                                        </Popover.Content>
                                    </Popover.Root>
                                </>
                            )
                        )}
                    </div>
                )}
            </Tree>
        </div>
    );
}
function BoardFavorite({ project }: { project: Project.TModel }) {
    const [t] = useTranslation();
    const starred = project.useField("starred");
    const { mutate, isPending } = useToggleStarProject();
    return (
        <Button
            type="button"
            size="icon"
            variant="ghost"
            className="size-6"
            aria-label={t("dashboard.Favorites")}
            aria-pressed={starred}
            disabled={isPending}
            onClick={() => mutate({ uid: project.uid })}
        >
            <IconComponent icon="star" size="3" className={starred ? "fill-current" : undefined} />
        </Button>
    );
}
