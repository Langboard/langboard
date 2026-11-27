import BaseGraphApprovalBotRequest from "@/models/bases/BaseGraphApprovalBotRequest";
import { Entity } from "typeorm";

@Entity({ name: "bot_trigger_graph_approval_request" })
class BotTriggerGraphApprovalRequest extends BaseGraphApprovalBotRequest {}

export default BotTriggerGraphApprovalRequest;
