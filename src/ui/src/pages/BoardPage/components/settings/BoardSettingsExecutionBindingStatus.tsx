import Alert from "@/components/base/Alert";
import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { useTranslation } from "react-i18next";

interface IBindingStatus {
    binding: { is_enabled: boolean } | null;
    binding_status: { state: "valid" | "binding_invalid" | "unconfigured"; reasons: string[] };
}

export default function BoardSettingsExecutionBindingStatus(): React.JSX.Element | null {
    const [t] = useTranslation();
    const { project } = useBoardSettings();
    const { query } = useQueryMutation();
    const { data, isError, refetch } = query(
        ["execution-binding-status", project.uid],
        async () => {
            return (await api.get(`/board/${project.uid}/settings/execution-binding`)).data as IBindingStatus;
        },
        { retry: 0 }
    );

    if (isError) {
        return (
            <Alert variant="destructive" title={t("project.settings.Execution binding unavailable")}>
                <Button size="sm" variant="outline" onClick={() => void refetch()}>
                    {t("common.Retry")}
                </Button>
            </Alert>
        );
    }
    if (!data?.binding) return null;
    if (data.binding_status.state === "valid") {
        return <Alert variant="success" title={t("project.settings.Execution binding valid")} />;
    }
    return (
        <Alert variant="destructive" title={t("project.settings.Execution binding invalid")}>
            <ul className="list-disc pl-5 text-sm">
                {data.binding_status.reasons.map((reason) => (
                    <li key={reason}>{t(`project.settings.executionBindingReasons.${reason}`, { defaultValue: reason })}</li>
                ))}
            </ul>
        </Alert>
    );
}
