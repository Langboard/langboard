import BaseModel, { BigIntColumn, TBigIntString } from "@/core/db/BaseModel";
import { getDatetimeType } from "@/core/db/DbType";
import SnowflakeID from "@/core/db/SnowflakeID";
import { Entity, Column } from "typeorm";

export enum ENotificationType {
    ProjectInvited = "project_invited",
    MentionedInCard = "mentioned_in_card",
    MentionedInComment = "mentioned_in_comment",
    MentionedInWiki = "mentioned_in_wiki",
    AssignedToCard = "assigned_to_card",
    ReactedToComment = "reacted_to_comment",
    NotifiedFromChecklist = "notified_from_checklist",
    CardDeadline = "card_deadline",
    ScheduledRule = "scheduled_rule",
}

@Entity({ name: "user_notification" })
class UserNotification extends BaseModel {
    @Column({ type: "varchar", nullable: false })
    public notifier_type!: string;

    @BigIntColumn(false)
    public notifier_id!: TBigIntString;

    @BigIntColumn(false)
    public receiver_id!: TBigIntString;

    @Column({ type: "varchar", enum: ENotificationType, nullable: false })
    public notification_type!: ENotificationType;

    @Column({ type: "json", default: {} })
    public message_vars: Record<string, unknown> = {};

    @Column({ type: "json", default: [] })
    public record_list: Array<[string, string]> = [];

    @Column({ type: getDatetimeType(), nullable: true, default: null })
    public read_at: Date | null = null;

    public static async read(uid: string, receiverId: TBigIntString): Promise<void> {
        const id = SnowflakeID.fromShortCode(uid).toString();
        await UserNotification.createQueryBuilder()
            .update({
                read_at: new Date(),
            })
            .where("id = :id", { id })
            .andWhere("receiver_id = :receiverId", { receiverId })
            .execute();
    }

    public static async readAll(receiverId: TBigIntString): Promise<void> {
        await UserNotification.createQueryBuilder()
            .update({ read_at: new Date() })
            .where("receiver_id = :receiverId", { receiverId })
            .andWhere("read_at IS NULL")
            .execute();
    }

    public static async deleteByUID(uid: string, receiverId: TBigIntString): Promise<void> {
        const id = SnowflakeID.fromShortCode(uid).toString();
        await UserNotification.createQueryBuilder()
            .where("id = :id", { id })
            .andWhere("receiver_id = :receiverId", { receiverId })
            .delete()
            .execute();
    }

    public static async deleteAll(receiverId: TBigIntString): Promise<void> {
        await UserNotification.createQueryBuilder().delete().where("receiver_id = :receiverId", { receiverId }).execute();
    }
}

export default UserNotification;
