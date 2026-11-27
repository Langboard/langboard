import BaseModel, { BigIntColumn, TBigIntString } from "@/core/db/BaseModel";
import { getDatetimeType } from "@/core/db/DbType";
import SnowflakeID from "@/core/db/SnowflakeID";
import { EAgentPermissionLevel } from "@langboard/core/ai";
import { Entity, Column } from "typeorm";

@Entity({ name: "chat_session" })
class ChatSession extends BaseModel {
    @BigIntColumn(false)
    public user_id!: TBigIntString;

    @Column({ type: "varchar" })
    public title!: string;

    @Column({ type: "varchar", default: EAgentPermissionLevel.Read })
    public api_permission_level!: EAgentPermissionLevel;

    @Column({ type: getDatetimeType(), nullable: true, default: null })
    public last_messaged_at: Date | null = null;

    public get apiResponse() {
        return {
            uid: this.uid,
            user_uid: this.user_id ? new SnowflakeID(this.user_id).toShortCode() : undefined,
            title: this.title,
            api_permission_level: this.api_permission_level,
            last_messaged_at: this.last_messaged_at,
            created_at: this.created_at,
            updated_at: this.updated_at,
        };
    }

    public static async findByUID(uid: string): Promise<ChatSession | null> {
        const id = SnowflakeID.fromShortCode(uid).toString();
        return this.findOne({ where: { id } });
    }

    public static async findByID(id: TBigIntString): Promise<ChatSession | null> {
        const session = await ChatSession.createQueryBuilder()
            .select([
                "cast(id as text) as converted_id",
                "cast(user_id as text) as converted_user_id",
                "title",
                "api_permission_level",
                "last_messaged_at",
            ])
            .where("id = :id", { id })
            .getRawOne();

        if (!session) {
            return null;
        }

        session.id = session.converted_id;
        session.user_id = session.converted_user_id;

        return ChatSession.create({
            ...session,
        });
    }

    public updateLastMessagedAt(date?: Date) {
        this.last_messaged_at = date || new Date();
        return ChatSession.update(this.id, {
            last_messaged_at: this.last_messaged_at,
        });
    }
}

export default ChatSession;
