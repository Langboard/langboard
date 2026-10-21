import { useMemo, useState } from "react";
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
function CollectionTree({ collections, projects, busy, save, navigate }: Props) {
    const [t] = useTranslation();
    const manager = useDragDropManager();
    const [failed, setFailed] = useState(false);
    const projectMap = useMemo(() => new Map(projects.map((project) => [project.uid, project])), [projects]);
    const data = useMemo(
        () =>
            collections.map(
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
        [collections, projectMap, t]
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
    return (
        <div
            onDragOver={(event) => {
                if (!busy && event.dataTransfer.types.includes(BOARD_REFERENCE_DRAG_TYPE)) {
                    event.preventDefault();
                    event.stopPropagation();
                    event.dataTransfer.dropEffect = "copy";
                }
            }}
            onDrop={(event) => {
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
                height={Math.min(320, Math.max(64, (collections.length + collections.reduce((sum, item) => sum + item.board_uids.length, 0)) * 32))}
                rowHeight={32}
                indent={12}
                openByDefault={false}
                dndManager={manager}
                disableMultiSelection
                disableEdit
                disableDrag={busy}
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
                    if (!dragged.boardUID && !parentId) {
                        const from = collections.findIndex((item) => item.uid === dragged.collectionUID);
                        const next = collections.filter((item) => item.uid !== dragged.collectionUID);
                        next.splice(index - (from < index ? 1 : 0), 0, collections[from]);
                        void persist(next);
                    } else if (dragged.boardUID && parentId) {
                        const source = collections.find((item) => item.uid === dragged.collectionUID);
                        const target = collections.find((item) => item.uid === parentId);
                        if (!source || !target || (source !== target && target.board_uids.includes(dragged.boardUID))) return;
                        const from = source.board_uids.indexOf(dragged.boardUID);
                        const next = collections.map((item) => ({
                            ...item,
                            board_uids: item.board_uids.filter((uid) => item.uid !== source.uid || uid !== dragged.boardUID),
                        }));
                        next.find((item) => item.uid === parentId)!.board_uids.splice(
                            index - (source === target && from < index ? 1 : 0),
                            0,
                            dragged.boardUID
                        );
                        void persist(next);
                    }
                }}
            >
                {({ node, style, dragHandle }) => (
                    <div
                        ref={dragHandle}
                        style={style}
                        data-collection-uid={node.data.collectionUID}
                        className="flex min-w-0 items-center gap-1"
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => event.stopPropagation()}
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
                            <BoardFavorite project={projectMap.get(node.data.boardUID)!} />
                        ) : (
                            !node.data.boardUID && (
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
