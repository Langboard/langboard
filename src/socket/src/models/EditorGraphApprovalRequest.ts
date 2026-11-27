import { TBigIntString } from "@/core/db/BaseModel";
import { TGraphApprovalResponse } from "@/models/GraphApprovalRequestTypes";
import BaseGraphApprovalRequestModel from "@/models/bases/BaseGraphApprovalRequestModel";
import { Column, Entity } from "typeorm";

@Entity({ name: "editor_graph_approval_request" })
class EditorGraphApprovalRequest extends BaseGraphApprovalRequestModel {
    @Column({ type: "varchar" })
    public document_name!: string;

    public toGraphApprovalResponse(projectID?: TBigIntString): TGraphApprovalResponse {
        return {
            ...this.getBaseApiResponse(projectID),
            document_name: this.document_name,
        };
    }
}

export default EditorGraphApprovalRequest;
