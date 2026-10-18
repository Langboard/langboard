"use client";

import React from "react";
import type { IMentionElement } from "@/components/Editor/plugins/markdown/mention";
import { IS_APPLE, KEYS, TComboboxInputElement } from "platejs";
import { cn } from "@/core/utils/ComponentUtils";
import { useMounted } from "@/core/hooks/useMounted";
import { BotModel, User } from "@/core/models";
import UserAvatar from "@/components/UserAvatar";
import UserAvatarDefaultList from "@/components/UserAvatarDefaultList";
import { useEditorData } from "@/core/providers/EditorDataProvider";
import { isModel, TUserLikeModel } from "@/core/models/ModelRegistry";
import { PlateElement, PlateElementProps, useFocused, useReadOnly, useSelected } from "platejs/react";
import {
    InlineCombobox,
    InlineComboboxContent,
    InlineComboboxEmpty,
    InlineComboboxGroup,
    InlineComboboxInput,
} from "@/components/plate-ui/inline-combobox";
import { useTranslation } from "react-i18next";
import { IMentionInputComboboxItemProps, MentionInputComboboxItem } from "@/components/plate-ui/mention-input-combobox-item";
import UserLikeComponent from "@/components/UserLikeComponent";

export type TMentionableUser = TUserLikeModel & {
    key: string;
    text: string;
};

export function MentionElement(
    props: PlateElementProps<IMentionElement> & {
        prefix?: string;
    }
) {
    const { prefix } = props;
    const element = props.element;
    const { mentionables, form } = useEditorData();
    const selected = useSelected();
    const focused = useFocused();
    const mounted = useMounted();
    const mentioned = mentionables.find((userOrBot) => userOrBot.uid === element.key) ?? User.Model.createUnknownUser();
    const readOnly = useReadOnly();

    let customName;
    if (mounted && IS_APPLE) {
        // Mac OS IME https://github.com/ianstormtaylor/slate/issues/3490
        customName = (
            <>
                {props.children}
                {prefix}
                <MentionLabel userOrBot={mentioned} />
            </>
        );
    } else {
        // Others like Android https://github.com/ianstormtaylor/slate/pull/5360
        customName = (
            <>
                {prefix}
                <MentionLabel userOrBot={mentioned} />
                {props.children}
            </>
        );
    }

    return (
        <PlateElement
            {...props}
            className={cn(
                "inline-block rounded-md bg-muted px-1.5 py-0.5 align-baseline text-sm font-medium",
                !readOnly && "cursor-pointer",
                selected && focused && "ring-2 ring-ring",
                element.children[0][KEYS.bold] === true && "font-bold",
                element.children[0][KEYS.italic] === true && "italic",
                element.children[0][KEYS.underline] === true && "underline"
            )}
            attributes={{
                ...props.attributes,
                contentEditable: false,
                "data-slate-value": element.value,
                draggable: true,
            }}
        >
            <UserAvatar.Root
                userOrBot={mentioned}
                withNameProps={{
                    noAvatar: true,
                    customName: customName,
                }}
            >
                <UserAvatarDefaultList
                    userOrBot={mentioned}
                    scope={{
                        projectUID: form?.project_uid,
                        cardUID: form?.card_uid,
                        wikiUID: form?.wiki_uid,
                    }}
                />
            </UserAvatar.Root>
        </PlateElement>
    );
}

export function MentionInputElement(props: PlateElementProps<TComboboxInputElement>) {
    const { editor, element } = props;
    const [t] = useTranslation();
    const { currentUser, mentionables: flatMentionables } = useEditorData();
    const [search, setSearch] = React.useState("");
    const mentionables = React.useMemo(() => {
        const userOrBots: IMentionInputComboboxItemProps["userOrBot"][] = [];
        for (let i = 0; i < flatMentionables.length; ++i) {
            const userOrBot = flatMentionables[i];
            if (userOrBot.uid === currentUser.uid) {
                continue;
            }

            let username;
            if (isModel(userOrBot, "User")) {
                if (!userOrBot.isValidUser()) {
                    continue;
                }
                username = userOrBot.username;
            } else if (isModel(userOrBot, "BotModel")) {
                username = userOrBot.bot_uname;
            } else {
                continue;
            }

            const fakeUser = userOrBot.asFake() as TMentionableUser;
            fakeUser.text = username;
            fakeUser.key = userOrBot.uid;

            userOrBots.push({ raw: userOrBot, value: fakeUser });
        }
        return userOrBots;
    }, [flatMentionables, currentUser.uid]);

    return (
        <PlateElement {...props} as="span">
            <InlineCombobox mobileEditorAnchor value={search} element={element} setValue={setSearch} showTrigger={false} trigger="@">
                <span className="inline-block rounded-md bg-muted px-1.5 py-0.5 align-baseline text-sm ring-ring focus-within:ring-2">
                    <InlineComboboxInput />
                </span>

                <InlineComboboxContent className="my-1.5">
                    <InlineComboboxEmpty>{t("editor.No results")}</InlineComboboxEmpty>

                    <InlineComboboxGroup>
                        {mentionables.map((mentionable) => (
                            <MentionInputComboboxItem key={mentionable.raw.uid} search={search} userOrBot={mentionable} editor={editor} />
                        ))}
                    </InlineComboboxGroup>
                </InlineComboboxContent>
            </InlineCombobox>

            {props.children}
        </PlateElement>
    );
}

const MentionLabel = ({ userOrBot }: { userOrBot: TUserLikeModel | IMentionElement }) => {
    if (!isModel(userOrBot, "User") && !isModel(userOrBot, "BotModel")) {
        return (userOrBot as IMentionElement).value;
    }

    return <UserLikeComponent userOrBot={userOrBot} userComp={MentionLabelUserItem} botComp={MentionLabelBotItem} props={{}} shouldReturnNull />;
};

const MentionLabelUserItem = ({ user }: { user: User.TModel }) => {
    const firstname = user.useField("firstname");
    const lastname = user.useField("lastname");

    return (
        <>
            {firstname} {lastname}
        </>
    );
};

const MentionLabelBotItem = ({ bot }: { bot: BotModel.TModel }) => {
    const name = bot.useField("name");

    return <>{name}</>;
};
