import { BigIntColumn, TBigIntString } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import { TGraphApprovalResponse } from "@/models/GraphApprovalRequestTypes";
import BaseGraphApprovalRequestModel from "@/models/bases/BaseGraphApprovalRequestModel";
import { Entity } from "typeorm";

@Entity({ name: "chat_graph_approval_request" })
class ChatGraphApprovalRequest extends BaseGraphApprovalRequestModel {
    @BigIntColumn(false)
    public chat_session_id!: TBigIntString;

    @BigIntColumn(false)
    public chat_history_id!: TBigIntString;

    public toGraphApprovalResponse(projectID?: TBigIntString): TGraphApprovalResponse {
        return {
            ...this.getBaseApiResponse(projectID),
            chat_session_uid: new SnowflakeID(this.chat_session_id).toShortCode(),
            chat_history_uid: new SnowflakeID(this.chat_history_id).toShortCode(),
        };
    }
}

export default ChatGraphApprovalRequest;
