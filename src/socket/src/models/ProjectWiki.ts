import BaseModel, { BigIntColumn, IEditorContentModel, TBigIntString } from "@/core/db/BaseModel";
import SnowflakeID from "@/core/db/SnowflakeID";
import ProjectWikiAssignedUser from "@/models/ProjectWikiAssignedUser";
import ProjectRole, { EProjectRoleAction } from "@/models/ProjectRole";
import { Entity, Column } from "typeorm";

@Entity({ name: "project_wiki" })
class ProjectWiki extends BaseModel {
    @BigIntColumn(false)
    public project_id!: TBigIntString;

    @Column({ type: "varchar" })
    public title!: string;

    @Column({ type: "json" })
    public content!: IEditorContentModel;

    @Column({ type: "integer" })
    public order!: number;

    @Column({ type: "boolean" })
    public is_public: bool = false;

    public static async isAssigned(userId: TBigIntString, wikiUID: string): Promise<bool> {
        const wikiId = SnowflakeID.fromShortCode(wikiUID).toString();
        const result = await ProjectWiki.createQueryBuilder()
            .leftJoinAndSelect(ProjectWikiAssignedUser, "pwa", "pwa.project_wiki_id = ProjectWiki.id")
            .where("ProjectWiki.id = :wikiId", { wikiId })
            .andWhere("(ProjectWiki.is_public is True OR pwa.user_id = :userId)", { userId })
            .limit(1)
            .getCount();

        return result > 0;
    }

    public static async canAccessCollaboration(userId: TBigIntString, wikiUID: string, isAdmin: bool = false): Promise<bool> {
        const wikiId = SnowflakeID.fromShortCode(wikiUID).toString();
        const wiki = await ProjectWiki.findOne({
            where: {
                id: wikiId,
            },
        });

        if (!wiki) {
            return false;
        }

        if (isAdmin) {
            return true;
        }

        if (wiki.is_public) {
            return true;
        }

        const isAssigned = await ProjectWiki.isAssigned(userId, wikiUID);
        if (isAssigned) {
            return true;
        }

        const projectUID = new SnowflakeID(wiki.project_id).toShortCode();
        return ProjectRole.isGranted(userId, projectUID, EProjectRoleAction.Update);
    }
}

export default ProjectWiki;
