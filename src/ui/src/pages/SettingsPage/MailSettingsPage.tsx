import Button from "@/components/base/Button";
import Card from "@/components/base/Card";
import MailTestMessage from "./MailTestMessage";
import MailDeliveryHistory from "./MailDeliveryHistory";
import { api } from "@/core/helpers/Api";
import { AuthUser } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

interface IMailConfiguration {
    server: string;
    port: number;
    starttls: boolean;
    ssl_tls: boolean;
    sender: string;
    username_configured: boolean;
    password_configured: boolean;
    authentication: "password" | "none";
    source: "environment";
}
interface IMailDiagnostic {
    checked_at: string;
    success: boolean;
    stages: Record<"dns" | "tcp" | "tls" | "auth", { status: "passed" | "failed" | "skipped" | "not_checked"; code: string | null }>;
    smtp_send_acceptance: "not_checked";
    inbox_delivery: "not_checked";
}
const safeCodes = ["configuration_invalid", "dns_failed", "connection_failed", "tls_failed", "authentication_failed", "timeout"];

export default function MailSettingsPage({ currentUser }: { currentUser: AuthUser.TModel }) {
    const [t] = useTranslation();
    const session = useAuthStore((state) => state.getSessionVersion());
    const isAdmin = useSyncExternalStore(
        useCallback((listener) => currentUser.subscribeFields(["is_admin"], listener), [currentUser]),
        () => currentUser.is_admin
    );
    if (!isAdmin)
        return (
            <p role="alert" className="p-6">
                {t("settings.mail.adminOnly")}
            </p>
        );
    return <MailSettings key={`${currentUser.uid}:${session}`} session={session} />;
}

function MailSettings({ session }: { session: number }) {
    const [t] = useTranslation();
    const [configuration, setConfiguration] = useState<IMailConfiguration | null>(null);
    const [diagnostic, setDiagnostic] = useState<IMailDiagnostic | null>(null);
    const [failure, setFailure] = useState<"load" | "diagnose" | "permission" | null>(null);
    const [busy, setBusy] = useState<"load" | "diagnose" | null>("load");
    const active = useRef(false);
    const pending = useRef(false);
    const revoked = useRef(false);
    const isCurrent = () => active.current && session === useAuthStore.getState().getSessionVersion();

    const revoke = () => {
        if (!isCurrent()) return;
        revoked.current = true;
        setConfiguration(null);
        setDiagnostic(null);
        setFailure("permission");
    };
    const canRequest = () => isCurrent() && !revoked.current;

    const request = async (kind: "load" | "diagnose") => {
        if (!isCurrent() || pending.current || revoked.current) return;
        pending.current = true;
        setBusy(kind);
        setFailure(null);
        if (kind === "diagnose") setDiagnostic(null);
        try {
            // Handle terminal permissions locally, without automatic auth replay or SMTP retries.
            const options = { validateStatus: () => true, env: { interceptToast: true } as never };
            const response =
                kind === "load"
                    ? await api.get<IMailConfiguration>("/settings/mail/configuration", options)
                    : await api.post<IMailDiagnostic>("/settings/mail/diagnostics", undefined, options);
            if (!canRequest()) return;
            if (response.status === 401 || response.status === 403) {
                revoke();
            } else if (response.status !== 200) {
                setFailure(kind === "load" ? "load" : "diagnose");
            } else if (kind === "load") {
                setConfiguration(response.data as IMailConfiguration);
            } else {
                setDiagnostic(response.data as IMailDiagnostic);
            }
        } catch {
            if (canRequest()) setFailure(kind === "load" ? "load" : "diagnose");
        } finally {
            if (isCurrent()) {
                pending.current = false;
                setBusy(null);
            }
        }
    };
    useEffect(() => {
        active.current = true;
        void request("load");
        return () => {
            active.current = false;
        };
    }, []);

    const presence = (value: boolean) => t(`settings.mail.${value ? "configured" : "notConfigured"}`);
    return (
        <div className="mx-auto w-full max-w-3xl space-y-4 p-4 sm:p-6">
            <h1 className="text-xl font-semibold">{t("settings.mail.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("settings.mail.environment")}</p>
            {failure && <p role="alert">{t(`settings.mail.${failure}Error`)}</p>}
            {busy === "load" && <p role="status">{t("common.Loading...")}</p>}
            {failure === "load" && <Button onClick={() => void request("load")}>{t("common.Retry")}</Button>}
            {configuration && (
                <Card.Root>
                    <Card.Header>
                        <Card.Title>{t("settings.mail.configuration")}</Card.Title>
                    </Card.Header>
                    <Card.Content>
                        <dl className="grid gap-3 sm:grid-cols-2">
                            {[
                                ["server", configuration.server],
                                ["port", configuration.port],
                                ["sender", configuration.sender],
                                ["starttls", presence(configuration.starttls)],
                                ["ssl_tls", presence(configuration.ssl_tls)],
                                ["authentication", t(`settings.mail.${configuration.authentication === "password" ? "passwordAuth" : "noAuth"}`)],
                                ["username", presence(configuration.username_configured)],
                                ["password", presence(configuration.password_configured)],
                            ].map(([label, value]) => (
                                <div key={label} className="min-w-0">
                                    <dt className="text-sm text-muted-foreground">{t(`settings.mail.${label}`)}</dt>
                                    <dd className="break-all">{value || "—"}</dd>
                                </div>
                            ))}
                        </dl>
                    </Card.Content>
                </Card.Root>
            )}
            {configuration && (
                <Button disabled={!!busy} onClick={() => void request("diagnose")}>
                    {t(`settings.mail.${busy === "diagnose" ? "testing" : "test"}`)}
                </Button>
            )}
            <p className="text-sm text-muted-foreground">{t("settings.mail.noDelivery")}</p>
            {configuration && <MailTestMessage isCurrent={canRequest} onPermissionDenied={revoke} />}
            {configuration && <MailDeliveryHistory isCurrent={canRequest} onPermissionDenied={revoke} />}
            <div aria-live="polite" aria-atomic="true">
                {diagnostic && (
                    <Card.Root>
                        <Card.Header>
                            <Card.Title>{t(`settings.mail.${diagnostic.success ? "success" : "failed"}`)}</Card.Title>
                            <p>
                                {t("settings.mail.checkedAt")}: <time dateTime={diagnostic.checked_at}>{diagnostic.checked_at}</time>
                            </p>
                        </Card.Header>
                        <Card.Content>
                            <dl className="space-y-3">
                                {(["dns", "tcp", "tls", "auth"] as const).map((stage) => {
                                    const result = diagnostic.stages[stage];
                                    const status = ["passed", "failed", "skipped", "not_checked"].includes(result.status)
                                        ? result.status
                                        : "not_checked";
                                    return (
                                        <div key={stage}>
                                            <dt className="font-medium">{t(`settings.mail.${stage}`)}</dt>
                                            <dd>
                                                {t(`settings.mail.${status}`)}
                                                {result.code && (
                                                    <> · {t(`settings.mail.${safeCodes.includes(result.code) ? result.code : "unknownError"}`)}</>
                                                )}
                                            </dd>
                                        </div>
                                    );
                                })}
                            </dl>
                        </Card.Content>
                    </Card.Root>
                )}
            </div>
        </div>
    );
}
