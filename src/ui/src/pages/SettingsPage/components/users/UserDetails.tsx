import Button from "@/components/base/Button";
import DateDistance from "@/components/DateDistance";
import MoreMenu from "@/components/MoreMenu";
import { api } from "@/core/helpers/Api";
import { User } from "@/core/models";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";
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
            {opened && <UserDetailsContent uid={user.uid} />}
        </MoreMenu.DialogItem>
    );
}

function UserDetailsContent({ uid }: { uid: string }) {
    const [t] = useTranslation();
    const [data, setData] = useState<IUserDetails | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        const abort = new AbortController();
        setData(null);
        setLoading(true);
        setError(false);
        api.get<{ user: IUserDetails }>(`/settings/users/${uid}`, { signal: abort.signal })
            .then(({ data }) => {
                if (!abort.signal.aborted) setData(data.user);
            })
            .catch(() => {
                if (!abort.signal.aborted) setError(true);
            })
            .finally(() => {
                if (!abort.signal.aborted) setLoading(false);
            });
        return () => abort.abort();
    }, [uid, revision]);

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
