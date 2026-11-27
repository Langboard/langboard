import BaseGraphApprovalBotRequest from "@/models/bases/BaseGraphApprovalBotRequest";
import { Entity } from "typeorm";

@Entity({ name: "bot_schedule_graph_approval_request" })
class BotScheduleGraphApprovalRequest extends BaseGraphApprovalBotRequest {}

export default BotScheduleGraphApprovalRequest;
