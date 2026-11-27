import BaseModel from "@/core/db/BaseModel";
import { EBotPlatform, EBotPlatformRunningType } from "@langboard/core/ai";
import { Entity, Column } from "typeorm";

export enum EInternalBotType {
    ProjectChat = "project_chat",
    EditorChat = "editor_chat",
    EditorCopilot = "editor_copilot",
}

@Entity({ name: "internal_bot" })
class InternalBot extends BaseModel {
    @Column({ type: "varchar", enum: EInternalBotType })
    public bot_type!: EInternalBotType;

    @Column({ type: "varchar" })
    public display_name!: string;

    @Column({ type: "varchar", enum: EBotPlatform })
    public platform!: EBotPlatform;

    @Column({ type: "varchar", enum: EBotPlatformRunningType })
    public platform_running_type!: EBotPlatformRunningType;

    @Column({ type: "varchar" })
    public api_url!: string;

    @Column({ type: "varchar" })
    public api_key: string = "";

    @Column({ type: "varchar" })
    public value?: string;

    @Column({ type: "json", nullable: true })
    public avatar: Record<string, unknown> | null = null;

    public get apiResponse() {
        return {
            uid: this.uid,
            bot_type: this.bot_type,
            display_name: this.display_name,
            avatar: this.avatar?.path ?? null,
            created_at: this.created_at,
            updated_at: this.updated_at,
        };
    }

    public static getSelectAllQuery(alias?: string): string[] {
        const prefix = alias ? `${alias}.` : "";
        return [
            `cast(${prefix}id as text) as converted_id`,
            `${prefix}bot_type as bot_type`,
            `${prefix}display_name as display_name`,
            `${prefix}platform as platform`,
            `${prefix}platform_running_type as platform_running_type`,
            `${prefix}api_url as api_url`,
            `${prefix}api_key as api_key`,
            `${prefix}value as value`,
            `${prefix}avatar as avatar`,
            `${prefix}created_at as created_at`,
            `${prefix}updated_at as updated_at`,
        ];
    }

    public static async findByType(type: EInternalBotType): Promise<InternalBot | null> {
        const internalBot = await InternalBot.createQueryBuilder()
            .select(InternalBot.getSelectAllQuery())
            .where("bot_type = :type", { type })
            .getRawOne();

        if (!internalBot) {
            return null;
        }

        internalBot.id = internalBot.converted_id;

        return InternalBot.create({
            ...internalBot,
        });
    }
}

export default InternalBot;
