import { CsvColumn, ROLE_ALL_GRANTED, TBigIntString, TRoleAllGranted } from "@/core/db/BaseModel";
import BaseRole from "@/models/bases/BaseRole";
import { Entity } from "typeorm";
import type { DataSource } from "typeorm";

export enum EApiKeyRoleAction {
    Read = "read",
    Create = "create",
    Update = "update",
    Delete = "delete",
}
export type TApiKeyRoleActions = EApiKeyRoleAction | keyof typeof EApiKeyRoleAction | TRoleAllGranted;

@Entity({ name: "api_key_role" })
class ApiKeyRole extends BaseRole {
    @CsvColumn()
    public actions!: TApiKeyRoleActions[];

    public static async isGranted(userId: TBigIntString, action: TApiKeyRoleActions, db?: DataSource): Promise<bool> {
        let apiKeyRole: ApiKeyRole | null;
        if (db) {
            const runner = db.createQueryRunner("master");
            try {
                await runner.connect();
                apiKeyRole = await runner.manager.findOne(ApiKeyRole, {
                    where: {
                        user_id: userId,
                    },
                });
            } finally {
                await runner.release();
            }
        } else {
            apiKeyRole = await ApiKeyRole.findOne({
                where: {
                    user_id: userId,
                },
            });
        }

        if (!apiKeyRole) {
            return false;
        }

        if (apiKeyRole.actions.includes(ROLE_ALL_GRANTED)) {
            return true;
        }

        return apiKeyRole.actions.includes(action);
    }

    public static async isAnyGranted(userId: TBigIntString, db?: DataSource): Promise<bool> {
        let apiKeyRole: ApiKeyRole | null;
        if (db) {
            const runner = db.createQueryRunner("master");
            try {
                await runner.connect();
                apiKeyRole = await runner.manager.findOne(ApiKeyRole, {
                    where: {
                        user_id: userId,
                    },
                });
            } finally {
                await runner.release();
            }
        } else {
            apiKeyRole = await ApiKeyRole.findOne({
                where: {
                    user_id: userId,
                },
            });
        }

        if (!apiKeyRole) {
            return false;
        }

        return apiKeyRole.actions.length > 0;
    }
}

export default ApiKeyRole;
