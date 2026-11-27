import { TBigIntString } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import { NullableBigIntColumn, TGraphApprovalResponse } from "@/models/GraphApprovalRequestTypes";
import BaseGraphApprovalRequestModel from "@/models/bases/BaseGraphApprovalRequestModel";

abstract class BaseGraphApprovalBotRequest extends BaseGraphApprovalRequestModel {
    @NullableBigIntColumn()
    public bot_id!: TBigIntString | null;

    @NullableBigIntColumn()
    public internal_bot_id!: TBigIntString | null;

    @NullableBigIntColumn()
    public bot_log_id!: TBigIntString | null;

    public toGraphApprovalResponse(projectID?: TBigIntString): TGraphApprovalResponse {
        return {
            ...this.getBaseApiResponse(projectID),
            ...(this.bot_id ? { bot_uid: new SnowflakeID(this.bot_id).toShortCode() } : {}),
            ...(this.internal_bot_id ? { internal_bot_uid: new SnowflakeID(this.internal_bot_id).toShortCode() } : {}),
            ...(this.bot_log_id ? { bot_log_uid: new SnowflakeID(this.bot_log_id).toShortCode() } : {}),
        };
    }
}

export default BaseGraphApprovalBotRequest;
