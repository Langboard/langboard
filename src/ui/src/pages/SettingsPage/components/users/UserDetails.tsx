import { UserIdentitySources } from "./UserIdentitySources";
import UserBusinessActivity from "./UserBusinessActivity";
import UserEffectivePermissions from "./UserEffectivePermissions";
import Button from "@/components/base/Button";
import DateDistance from "@/components/DateDistance";
import MoreMenu from "@/components/MoreMenu";
import { api } from "@/core/helpers/Api";
import { User } from "@/core/models";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";
import useAuthStore from "@/core/stores/AuthStore";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface IUserDetails {
    uid: string;
    firstname: string;
    lastname: string;
    username: string;
    email: string;
    affiliation?: string | null;
    position?: string | null;
    account_status: "active" | "inactive";
    created_at: string;
    last_signed_in_at: string | null;
    latest_business_activity_at: string | null;
    identity_sources: User.Interface["identity_sources"];
    is_admin: bool;
    setting_role_actions: string[];
    api_key_role_actions: string[];
    mcp_role_actions: string[];
}

function UserDetails({ user }: { user: User.TModel }) {
    const [t] = useTranslation();
    const [opened, setOpened] = useState(false);

    return (
        <MoreMenu.DialogItem
            menuName={t("settings.User details")}
            useButtons={false}
            onOpenChange={setOpened}
            contentProps={{ className: "max-w-xl", "aria-label": t("settings.User details") }}
        >
            {opened && <UserDetailsContent key={user.uid} uid={user.uid} />}
        </MoreMenu.DialogItem>
    );
}

function UserDetailsContent({ uid }: { uid: string }) {
    const [t] = useTranslation();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const [revision, setRevision] = useState(0);
    const identity = JSON.stringify([actorUID, sessionVersion, uid, revision]);
    const [result, setResult] = useState<{ identity: string; data?: IUserDetails; error?: boolean }>();

    useEffect(() => {
        if (!actorUID) return;
        const abort = new AbortController();
        const current = () =>
            !abort.signal.aborted &&
            actorUID === useAuthStore.getState().currentUser?.uid &&
            sessionVersion === useAuthStore.getState().getSessionVersion();
        api.get<{ user: IUserDetails }>(`/settings/users/${uid}`, { signal: abort.signal, env: { interceptToast: true } as never })
            .then(({ data }) => {
                if (current()) setResult({ identity, data: data.user });
            })
            .catch(() => {
                if (current()) setResult({ identity, error: true });
            });
        return () => abort.abort();
    }, [actorUID, sessionVersion, uid, identity]);

    const current = actorUID && result?.identity === identity ? result : undefined;
    const data = current?.data;
    const loading = !!actorUID && !current;
    const error = !actorUID || current?.error;

    const fields = data
        ? [
              [t("settings.User ID"), data.uid],
              [t("user.Username"), data.username],
              [t("user.Email"), data.email],
              [t("user.First Name"), data.firstname],
              [t("user.Last Name"), data.lastname],
              [t("user.Affiliation"), data.affiliation || "—"],
              [t("user.Position"), data.position || "—"],
              [t("settings.Account status"), t(data.account_status === "active" ? "settings.Active" : "settings.Inactive")],
          ]
        : [];

    return (
        <section aria-busy={loading} className="space-y-4">
            <div className="flex items-center justify-between gap-3 pr-6">
                <h2 className="text-lg font-semibold">{t("settings.User details")}</h2>
                <Button variant="outline" size="sm" disabled={loading} onClick={() => setRevision((value) => value + 1)}>
                    {t("common.Refresh")}
                </Button>
            </div>
            {loading && <p role="status">{t("common.Loading...")}</p>}
            {error && <p role="alert">{t("settings.User details failed")}</p>}
            {data && (
                <>
                    <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-2 text-sm">
                        {fields.map(([label, value]) => (
                            <div key={label} className="contents">
                                <dt className="text-muted-foreground">{label}</dt>
                                <dd className="break-words">{value}</dd>
                            </div>
                        ))}
                        <dt className="text-muted-foreground">{t("settings.Identity source")}</dt>
                        <dd>
                            <UserIdentitySources sources={data.identity_sources} />
                        </dd>
                        <dt className="text-muted-foreground">{t("settings.Created")}</dt>
                        <dd>
                            <DateDistance date={new Date(data.created_at)} />
                        </dd>
                        <dt className="text-muted-foreground">{t("settings.Last successful sign-in")}</dt>
                        <dd>
                            {data.last_signed_in_at ? <DateDistance date={new Date(data.last_signed_in_at)} /> : t("settings.No recorded sign-in")}
                        </dd>
                        <dt className="text-muted-foreground">{t("settings.Latest business activity")}</dt>
                        <dd>
                            {data.latest_business_activity_at ? (
                                <DateDistance
                                    date={new Date(data.latest_business_activity_at)}
                                    title={new Date(data.latest_business_activity_at).toISOString()}
                                />
                            ) : (
                                t("settings.No recorded business activity")
                            )}
                        </dd>
                    </dl>
                    <UserBusinessActivity key={uid} uid={uid} />
                    <UserEffectivePermissions key={`permissions:${uid}`} uid={uid} />
                    <section className="space-y-2 border-t pt-4">
                        <h3 className="font-semibold">{t("settings.Configured permissions")}</h3>
                        <p className="text-sm text-muted-foreground">{t("settings.Configured permissions description")}</p>
                        <p className="text-sm">{t(data.is_admin ? "settings.Admin" : "settings.No Admin permissions")}</p>
                        {(
                            [
                                ["setting", t("settings.Admin"), data.setting_role_actions],
                                ["api_key", t("settings.API key"), data.api_key_role_actions],
                                ["mcp", t("settings.MCP"), data.mcp_role_actions],
                            ] as const
                        ).map(([scope, label, actions]) => (
                            <div key={scope} className="text-sm">
                                <h4 className="font-medium">{label}</h4>
                                <p className="break-words text-muted-foreground">
                                    {actions.length
                                        ? actions
                                              .map((action) =>
                                                  action === ROLE_ALL_GRANTED
                                                      ? t("settings.Full access")
                                                      : t(`role.${scope}.${action}`, { defaultValue: action })
                                              )
                                              .join(", ")
                                        : t("settings.No Permissions")}
                                </p>
                            </div>
                        ))}
                    </section>
                </>
            )}
        </section>
    );
}

export default UserDetails;
