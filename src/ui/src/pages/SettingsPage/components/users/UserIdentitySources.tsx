import { User } from "@/core/models";
import { useTranslation } from "react-i18next";

export function UserIdentitySources({ sources }: { sources?: User.Interface["identity_sources"] }) {
    const [t] = useTranslation();
    return (
        <span>
            {sources === undefined
                ? t("settings.Identity source unavailable")
                : sources.length
                  ? sources.map((source) => t(source === "oidc" ? "settings.OIDC linked" : "settings.SCIM provisioned")).join(", ")
                  : t("settings.No recorded identity source")}
        </span>
    );
}

export default function UserListIdentitySources({ user }: { user: User.TModel }) {
    const sources = user.useField("identity_sources");
    return <UserIdentitySources sources={sources} />;
}
