import { useTranslation } from "react-i18next";
import Input from "@/components/base/Input";
import type { DevtronScope } from "@/core/apps/DevtronSettings";

export default function DevtronScopeFields({
    value,
    onChange,
    inventory = false,
}: {
    value: DevtronScope;
    onChange: (value: DevtronScope) => void;
    inventory?: boolean;
}) {
    const [t] = useTranslation();
    return (
        <div className="flex min-w-0 flex-col gap-2">
            <label className="flex min-w-0 flex-col gap-1 text-sm">
                {t("project.settings.devtron.scopeKind")}
                <select
                    className="select min-h-10 w-full min-w-0 rounded-md border bg-background px-3"
                    value={value.kind}
                    onChange={(event) =>
                        onChange({
                            kind: event.target.value as DevtronScope["kind"],
                            first: "",
                            second: "",
                        })
                    }
                >
                    {inventory && <option value="inventory">{t("project.settings.devtron.inventory")}</option>}
                    <option value="native">{t("project.settings.devtron.native")}</option>
                    <option value="installed">{t("project.settings.devtron.installed")}</option>
                    <option value="external">{t("project.settings.devtron.external")}</option>
                </select>
            </label>
            {value.kind !== "inventory" && (
                <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {t(`project.settings.devtron.${value.kind === "external" ? "helmTuple" : value.kind === "installed" ? "installedID" : "appID"}`)}
                    <Input
                        value={value.first}
                        onChange={(event) => onChange({ ...value, first: event.target.value })}
                        inputMode={value.kind === "external" ? "text" : "numeric"}
                        autoComplete="off"
                    />
                </label>
            )}
            {value.kind === "native" && (
                <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {t("project.settings.devtron.environmentID")}
                    <Input
                        value={value.second}
                        onChange={(event) => onChange({ ...value, second: event.target.value })}
                        inputMode="numeric"
                        autoComplete="off"
                    />
                </label>
            )}
        </div>
    );
}
