import Button from "@/components/base/Button";
import Card from "@/components/base/Card";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface ITestResult {
    checked_at?: string;
    smtp_send_acceptance: "accepted" | "failed" | "unknown";
    code?: string | null;
}
const safeCodes = [
    "configuration_invalid",
    "authentication_failed",
    "smtp_rejected",
    "tls_failed",
    "connection_failed",
    "timeout",
    "transport_error",
];

export default function MailTestMessage({ isCurrent, onPermissionDenied }: { isCurrent: () => boolean; onPermissionDenied: () => void }) {
    const [t] = useTranslation();
    const [recipient, setRecipient] = useState("");
    const [busy, setBusy] = useState(false);
    const [invalid, setInvalid] = useState(false);
    const [result, setResult] = useState<ITestResult | null>(null);
    const pending = useRef(false);
    const active = useRef(true);
    useEffect(() => {
        active.current = true;
        return () => {
            active.current = false;
        };
    }, []);
    const current = () => active.current && isCurrent();
    const send = async () => {
        if (!current() || pending.current) return;
        // Require one bare mailbox; native email validity also handles the remaining syntax.
        const controlCharacter = [...recipient].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);
        if (recipient.length > 254 || controlCharacter || /[\s,;<>]/.test(recipient) || !/^[^@]+@[^@]+\.[^@]+$/.test(recipient)) {
            setInvalid(true);
            return;
        }
        pending.current = true;
        setBusy(true);
        setInvalid(false);
        setResult(null);
        try {
            const response = await api.post<ITestResult>(
                "/settings/mail/test-message",
                { recipient },
                {
                    validateStatus: () => true,
                    env: { interceptToast: true } as never,
                }
            );
            if (!current()) return;
            if (response.status === 401 || response.status === 403) {
                onPermissionDenied();
                return;
            }
            if (response.status === 422 || response.status === 400) {
                setInvalid(true);
            } else if (response.status === 200 && ["accepted", "failed", "unknown"].includes(response.data.smtp_send_acceptance)) {
                setResult(response.data);
            } else {
                setResult({ smtp_send_acceptance: "unknown" });
            }
        } catch {
            if (current()) setResult({ smtp_send_acceptance: "unknown" });
        } finally {
            if (current()) {
                pending.current = false;
                setBusy(false);
            }
        }
    };
    return (
        <Card.Root>
            <Card.Header>
                <Card.Title>{t("settings.mail.testMessage")}</Card.Title>
            </Card.Header>
            <Card.Content className="space-y-3">
                <p>{t("settings.mail.testMessageHelp")}</p>
                <form
                    className="space-y-3"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void send();
                    }}
                >
                    <label className="block space-y-1">
                        <span>{t("settings.mail.recipient")}</span>
                        <Input
                            type="email"
                            required
                            maxLength={254}
                            autoComplete="off"
                            value={recipient}
                            disabled={busy}
                            aria-describedby="mail-recipient-help"
                            aria-invalid={invalid}
                            onChange={(event) => {
                                setRecipient(event.target.value);
                                setInvalid(false);
                            }}
                        />
                    </label>
                    <p id="mail-recipient-help" className="text-sm">
                        {t("settings.mail.singleRecipient")}
                    </p>
                    {invalid && <p role="alert">{t("settings.mail.invalidRecipient")}</p>}
                    <Button type="submit" disabled={busy}>
                        {t(`settings.mail.${busy ? "sendingTest" : "sendTest"}`)}
                    </Button>
                </form>
                <div aria-live="polite" aria-atomic="true">
                    {result && (
                        <div className="space-y-2">
                            <p>{t(`settings.mail.send_${result.smtp_send_acceptance}`)}</p>
                            <p>{t("settings.mail.inboxUnverified")}</p>
                            {result.checked_at && (
                                <p>
                                    {t("settings.mail.checkedAt")}: <time dateTime={result.checked_at}>{result.checked_at}</time>
                                </p>
                            )}
                            {result.code && <p>{t(`settings.mail.${safeCodes.includes(result.code) ? result.code : "unknownError"}`)}</p>}
                        </div>
                    )}
                </div>
            </Card.Content>
        </Card.Root>
    );
}
