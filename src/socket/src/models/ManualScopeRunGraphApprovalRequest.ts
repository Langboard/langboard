import BaseGraphApprovalBotRequest from "@/models/bases/BaseGraphApprovalBotRequest";
import { Entity } from "typeorm";

@Entity({ name: "manual_scope_run_graph_approval_request" })
class ManualScopeRunGraphApprovalRequest extends BaseGraphApprovalBotRequest {}

export default ManualScopeRunGraphApprovalRequest;
