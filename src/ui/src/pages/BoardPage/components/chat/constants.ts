import { EAgentPermissionLevel } from "@langboard/core/ai";

export const INLINE_CHAT_ACTIONS_WIDTH = 120;
export const FULL_CHAT_SEND_AREA_WIDTH = 230;
export const COMPACT_CHAT_SEND_AREA_WIDTH = 164;
export const CHAT_INPUT_MIN_HEIGHT = 80;

export const CHAT_PERMISSION_LEVEL_OPTIONS = [EAgentPermissionLevel.Read, EAgentPermissionLevel.Edit, EAgentPermissionLevel.FullAccess];
export const CHAT_PERMISSION_LEVEL_ICONS: Record<EAgentPermissionLevel, string> = {
    [EAgentPermissionLevel.Read]: "eye",
    [EAgentPermissionLevel.Edit]: "pencil",
    [EAgentPermissionLevel.FullAccess]: "key-round",
};
