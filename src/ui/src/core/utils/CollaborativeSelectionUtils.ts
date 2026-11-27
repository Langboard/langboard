import { TUserLikeModel } from "@/core/models/ModelRegistry";
import { Utils } from "@langboard/core/utils";

export const parseCollaborativeStringList = (value: string, fallback: string[]) => {
    try {
        const parsed = JSON.parse(value);
        if (!Array.isArray(parsed) || parsed.some((item) => !Utils.Type.isString(item))) {
            return fallback;
        }

        return parsed;
    } catch {
        return fallback;
    }
};

export const parseCollaborativeMemberUIDs = (value: string, fallback: string[]) => {
    return parseCollaborativeStringList(value, fallback).map((item) => (item.startsWith("member:") ? item.slice("member:".length) : item));
};

export const getUserUIDs = (users: (string | TUserLikeModel)[]) => {
    return users
        .map((user) => (Utils.Type.isString(user) ? "" : user?.uid))
        .filter((uid): uid is string => Utils.Type.isString(uid) && uid.length > 0);
};

export const isAssignableWikiMember = (item: TUserLikeModel, currentUserUID: string) => {
    return item.uid !== currentUserUID && !("is_admin" in item && item.is_admin) && !("isValidUser" in item && !item.isValidUser());
};
