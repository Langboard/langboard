import { getDatetimeType } from "@/core/db/DbType";
import BaseModel, { TBigIntString } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import { Column, Entity } from "typeorm";
import { EGraphApprovalOriginType, EGraphApprovalStatus, NullableBigIntColumn, TGraphApprovalResponse } from "@/models/GraphApprovalRequestTypes";

@Entity({ name: "graph_approval_request" })
class GraphApprovalRequest extends BaseModel {
    public approvalResponse?: TGraphApprovalResponse;

    @NullableBigIntColumn()
    public requested_by_user_id!: TBigIntString | null;

    @NullableBigIntColumn()
    public resolved_by_user_id!: TBigIntString | null;

    @Column({ type: "varchar" })
    public thread_id!: string;

    @Column({ type: "varchar" })
    public run_id!: string;

    @Column({ type: "varchar", enum: EGraphApprovalOriginType })
    public request_type!: EGraphApprovalOriginType;

    public get origin_type() {
        return this.request_type;
    }

    @Column({ type: "varchar" })
    public action_type!: string;

    @Column({ type: "varchar" })
    public permission!: string;

    @Column({ type: "varchar", nullable: true })
    public tool_name!: string | null;

    @Column({ type: "varchar", nullable: true })
    public api_name!: string | null;

    @Column({ type: "json" })
    public request_payload!: Record<string, unknown>;

    @Column({ type: "json" })
    public preview_payload!: Record<string, unknown>;

    @Column({ type: "varchar", enum: EGraphApprovalStatus })
    public status!: EGraphApprovalStatus;

    @Column({ type: getDatetimeType(), nullable: true, default: null })
    public resolved_at!: Date | null;

    @Column({ type: getDatetimeType(), nullable: true, default: null })
    public expires_at!: Date | null;

    @Column({ type: "varchar", nullable: true })
    public rejection_reason!: string | null;

    public get apiResponse() {
        return {
            uid: this.uid,
            thread_id: this.thread_id,
            run_id: this.run_id,
            origin_type: this.request_type,
            action_type: this.action_type,
            permission: this.permission,
            preview_payload: this.preview_payload,
            status: this.status,
            created_at: this.created_at,
            updated_at: this.updated_at,
            ...(this.requested_by_user_id ? { requested_by_user_uid: new SnowflakeID(this.requested_by_user_id).toShortCode() } : {}),
            ...(this.resolved_by_user_id ? { resolved_by_user_uid: new SnowflakeID(this.resolved_by_user_id).toShortCode() } : {}),
            ...(this.tool_name ? { tool_name: this.tool_name } : {}),
            ...(this.api_name ? { api_name: this.api_name } : {}),
            ...(this.resolved_at ? { resolved_at: this.resolved_at } : {}),
            ...(this.expires_at ? { expires_at: this.expires_at } : {}),
            ...(this.rejection_reason ? { rejection_reason: this.rejection_reason } : {}),
            ...this.approvalResponse,
        };
    }
}

export default GraphApprovalRequest;
