import { BigIntColumn, CsvColumn, ROLE_ALL_GRANTED, TBigIntString, TRoleAllGranted } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import BaseRole from "@/models/bases/BaseRole";
import { Entity } from "typeorm";
import type { DataSource } from "typeorm";

export enum EProjectRoleAction {
    Read = "read",
    Update = "update",
    CardWrite = "card_write",
    CardUpdate = "card_update",
    CardDelete = "card_delete",
}
export type TProjectRoleActions = EProjectRoleAction | keyof typeof EProjectRoleAction | TRoleAllGranted;

@Entity({ name: "project_role" })
class ProjectRole extends BaseRole {
    @BigIntColumn(false)
    public project_id!: TBigIntString;

    @CsvColumn()
    public actions!: TProjectRoleActions[];

    public static async isGranted(userId: TBigIntString, projectUID: string, action: TProjectRoleActions, db?: DataSource): Promise<bool> {
        const projectId = SnowflakeID.fromShortCode(projectUID).toString();
        let projectRole: ProjectRole | null;
        if (db) {
            const runner = db.createQueryRunner("master");
            try {
                await runner.connect();
                projectRole = await runner.manager.findOne(ProjectRole, {
                    where: {
                        user_id: userId,
                        project_id: projectId,
                    },
                });
            } finally {
                await runner.release();
            }
        } else {
            projectRole = await ProjectRole.findOne({
                where: {
                    user_id: userId,
                    project_id: projectId,
                },
            });
        }

        if (!projectRole) {
            return false;
        }

        if (projectRole.actions.includes(ROLE_ALL_GRANTED)) {
            return true;
        }

        return projectRole.actions.includes(action);
    }
}

export default ProjectRole;
