import BaseModel, { BigIntColumn, TBigIntString } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import { Column } from "typeorm";
import { EGraphApprovalScopeTable, NullableBigIntColumn, TGraphApprovalResponse } from "@/models/GraphApprovalRequestTypes";

abstract class BaseGraphApprovalRequestModel extends BaseModel {
    @BigIntColumn(false)
    public approval_request_id!: TBigIntString;

    @Column({ type: "varchar", enum: EGraphApprovalScopeTable })
    public scope_table!: EGraphApprovalScopeTable;

    @NullableBigIntColumn()
    public scope_id!: TBigIntString | null;

    protected getBaseApiResponse(projectID?: TBigIntString): TGraphApprovalResponse {
        return {
            scope_table: this.scope_table,
            ...(projectID ? { project_uid: new SnowflakeID(projectID).toShortCode() } : {}),
            ...(this.scope_id ? { scope_uid: new SnowflakeID(this.scope_id).toShortCode() } : {}),
        };
    }
}

export default BaseGraphApprovalRequestModel;
