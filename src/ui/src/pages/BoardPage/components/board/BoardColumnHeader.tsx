import Card from "@/components/base/Card";
import BoardColumnDescription from "@/pages/BoardPage/components/board/BoardColumnDescription";
import { ProjectColumn } from "@/core/models";
import BoardColumnMoreMenu from "@/pages/BoardPage/components/board/BoardColumnMoreMenu";
import BoardGraphApprovalTargetBadge from "@/pages/BoardPage/components/board/BoardGraphApprovalTargetBadge";
import BoardColumnName, { IBoardColumnNameRef } from "@/pages/BoardPage/components/board/BoardColumnName";
import { EGraphApprovalScopeTable } from "@/core/models/GraphApprovalRequestModel";
import { lazy, memo, Suspense, useCallback, useRef, useState } from "react";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import { useBoard } from "@/core/providers/BoardProvider";
import { useTranslation } from "react-i18next";
import { DISABLE_DRAGGING_ATTR } from "@/constants";

const BoardArchivedCards = lazy(() => import("./BoardArchivedCards"));

export interface IBoardColumnHeaderProps {
    isDragging: bool;
    column: ProjectColumn.TModel;
    headerProps: React.DetailedHTMLProps<React.HTMLAttributes<HTMLDivElement>, HTMLDivElement>;
}

const BoardColumnHeader = memo(({ isDragging, column, headerProps }: IBoardColumnHeaderProps) => {
    const columnNameRef = useRef<IBoardColumnNameRef>(null);
    const [t] = useTranslation();
    const { project } = useBoard();
    const visibleDays = project.useField("archive_visible_days") ?? 3;
    const isArchive = column.useField("is_archive");
    const [archiveOpen, setArchiveOpen] = useState(false);

    const handleRenameStart = useCallback(() => {
        columnNameRef.current?.startEditing();
    }, []);

    return (
        <Card.Header
            className="flex flex-row items-start justify-between space-y-0 pb-1 pr-3 pt-4 text-left font-semibold"
            {...headerProps}
        >
            <div className="flex min-w-0 flex-1 items-center gap-2">
                <BoardColumnName ref={columnNameRef} isDragging={isDragging} column={column} />
                <BoardColumnDescription column={column} />
                <BoardGraphApprovalTargetBadge
                    projectUID={column.project_uid}
                    scopeTable={EGraphApprovalScopeTable.ProjectColumn}
                    scopeUID={column.uid}
                    className="flex-shrink-0"
                />
            </div>
            {isArchive && (
                <>
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("project.View all archived cards")}
                        title={t("project.Archived cards are hidden from the default board after {{days}} days, not deleted.", {
                            days: visibleDays,
                        })}
                        onClick={() => setArchiveOpen(true)}
                        {...{ [DISABLE_DRAGGING_ATTR]: "" }}
                    >
                        <IconComponent icon="archive" className="size-4" />
                    </Button>
                    {archiveOpen && (
                        <Suspense fallback={null}>
                            <BoardArchivedCards onClose={() => setArchiveOpen(false)} />
                        </Suspense>
                    )}
                </>
            )}
            <BoardColumnMoreMenu column={column} onRenameStart={handleRenameStart} />
        </Card.Header>
    );
});
BoardColumnHeader.displayName = "Board.ColumnHeader";

export default BoardColumnHeader;
