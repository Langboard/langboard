"use client";

import { getMentionOnSelectItem } from "@platejs/mention";
import { InlineComboboxItem } from "@/components/plate-ui/inline-combobox";
import { BotModel, User } from "@/core/models";
import { PlateEditor } from "platejs/react";
import type { TMentionableUser } from "@/components/plate-ui/mention-node";
import UserLikeComponent from "@/components/UserLikeComponent";
import { TUserLikeModel } from "@/core/models/ModelRegistry";
import UserAvatarTrigger from "@/components/UserAvatar/Trigger";

const onSelectItem = getMentionOnSelectItem();

export interface IMentionInputComboboxItemProps {
    search: string;
    userOrBot: { raw: TUserLikeModel; value: TMentionableUser };
    editor: PlateEditor;
}

interface IMentionInputComboboxItemInnerProps extends IMentionInputComboboxItemProps {
    value: TMentionableUser;
}

export const MentionInputComboboxItem = ({ userOrBot, ...props }: IMentionInputComboboxItemProps) => {
    return (
        <UserLikeComponent
            userOrBot={userOrBot.raw}
            userComp={MentionInputComboboxUserItem}
            botComp={MentionInputComboboxBotItem}
            props={{
                ...props,
                value: userOrBot.value,
            }}
            shouldReturnNull
        />
    );
};

const MentionInputComboboxUserItem = ({ user, value, ...props }: Omit<IMentionInputComboboxItemInnerProps, "userOrBot"> & { user: User.TModel }) => {
    const firstname = user.useField("firstname");
    const lastname = user.useField("lastname");
    const username = user.useField("username");

    return (
        <MentionInputComboboxItemInner
            {...props}
            value={`${firstname} ${lastname} ${username}`}
            username={username}
            names={`${firstname} ${lastname}`}
            userOrBot={{
                raw: user,
                value,
            }}
        />
    );
};

const MentionInputComboboxBotItem = ({ bot, value, ...props }: Omit<IMentionInputComboboxItemInnerProps, "userOrBot"> & { bot: BotModel.TModel }) => {
    const name = bot.useField("name");
    const username = bot.useField("bot_uname");

    return (
        <MentionInputComboboxItemInner
            {...props}
            value={`${name} ${username}`}
            username={username}
            names={name}
            userOrBot={{
                raw: bot,
                value,
            }}
        />
    );
};

const MentionInputComboboxItemInner = ({
    search,
    value,
    username,
    names,
    userOrBot,
    editor,
}: Omit<IMentionInputComboboxItemInnerProps, "value"> & { value: string; username: string; names: string }) => {
    return (
        <InlineComboboxItem value={value} onClick={() => onSelectItem(editor, userOrBot.value, search)} className="h-auto p-0">
            <UserAvatarTrigger
                userOrBot={userOrBot.raw}
                withNameProps={{
                    className: "gap-1 p-1 w-full",
                    customName: (
                        <div>
                            <div className="text-sm leading-none">{names}</div>
                            <div className="text-xs text-muted-foreground/50">@{username}</div>
                        </div>
                    ),
                }}
            />
        </InlineComboboxItem>
    );
};
