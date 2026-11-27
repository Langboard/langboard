import { TBigIntString } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import { Utils } from "@langboard/core/utils";
import { Column } from "typeorm";

export enum EGraphApprovalOriginType {
    Chat = "chat",
    Editor = "editor",
    Trigger = "trigger",
    Schedule = "schedule",
    ManualScopeRun = "manual_scope_run",
}

export enum EGraphApprovalScopeTable {
    Project = "project",
    ProjectColumn = "project_column",
    Card = "card",
    ProjectWiki = "project_wiki",
}

export enum EGraphApprovalStatus {
    Pending = "pending",
    Approved = "approved",
    Rejected = "rejected",
    Expired = "expired",
    Cancelled = "cancelled",
    Resolved = "resolved",
}

const nullableBigIntTransformer = {
    to: (value: TBigIntString | undefined): TBigIntString | null => value?.toString() ?? null,
    from: (value: TBigIntString | undefined): TBigIntString | null => value?.toString() ?? null,
};

export const NullableBigIntColumn = () => Column("bigint", { nullable: true, transformer: [nullableBigIntTransformer] });

export type TGraphApprovalResponse = {
    project_uid?: string;
    scope_table?: EGraphApprovalScopeTable;
    scope_uid?: string;
    bot_uid?: string;
    internal_bot_uid?: string;
    bot_log_uid?: string;
    chat_session_uid?: string;
    chat_history_uid?: string;
    document_name?: string;
};

export const getGraphApprovalScopeID = ({
    projectID,
    scopeTable,
    scopeUID,
}: {
    projectID: TBigIntString;
    scopeTable: EGraphApprovalScopeTable;
    scopeUID?: unknown;
}) => {
    if (Utils.Type.isString(scopeUID)) {
        return SnowflakeID.fromShortCode(scopeUID).toString();
    }
    if (scopeTable === EGraphApprovalScopeTable.Project) {
        return projectID;
    }
    return null;
};
