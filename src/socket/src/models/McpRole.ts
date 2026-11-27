import { CsvColumn, ROLE_ALL_GRANTED, TBigIntString, TRoleAllGranted } from "@/core/db/BaseModel";
import BaseRole from "@/models/bases/BaseRole";
import { Entity } from "typeorm";
import type { DataSource } from "typeorm";

export enum EMcpRoleAction {
    Read = "read",
    Create = "create",
    Update = "update",
    Delete = "delete",
}
export type TMcpRoleActions = EMcpRoleAction | keyof typeof EMcpRoleAction | TRoleAllGranted;

@Entity({ name: "mcp_role" })
class McpRole extends BaseRole {
    @CsvColumn()
    public actions!: TMcpRoleActions[];

    public static async isGranted(userId: TBigIntString, action: TMcpRoleActions, db?: DataSource): Promise<bool> {
        let mcpRole: McpRole | null;
        if (db) {
            const runner = db.createQueryRunner("master");
            try {
                await runner.connect();
                mcpRole = await runner.manager.findOne(McpRole, {
                    where: {
                        user_id: userId,
                    },
                });
            } finally {
                await runner.release();
            }
        } else {
            mcpRole = await McpRole.findOne({
                where: {
                    user_id: userId,
                },
            });
        }

        if (!mcpRole) {
            return false;
        }

        if (mcpRole.actions.includes(ROLE_ALL_GRANTED)) {
            return true;
        }

        return mcpRole.actions.includes(action);
    }

    public static async isAnyGranted(userId: TBigIntString, db?: DataSource): Promise<bool> {
        let mcpRole: McpRole | null;
        if (db) {
            const runner = db.createQueryRunner("master");
            try {
                await runner.connect();
                mcpRole = await runner.manager.findOne(McpRole, {
                    where: {
                        user_id: userId,
                    },
                });
            } finally {
                await runner.release();
            }
        } else {
            mcpRole = await McpRole.findOne({
                where: {
                    user_id: userId,
                },
            });
        }

        if (!mcpRole) {
            return false;
        }

        return mcpRole.actions.length > 0;
    }
}

export default McpRole;
