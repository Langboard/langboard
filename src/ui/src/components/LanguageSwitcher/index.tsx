import { LANGUAGE_LOCALES } from "@/constants";
import { useTranslation } from "react-i18next";
import Button, { type ButtonProps } from "@/components/base/Button";
import DropdownMenu from "@/components/base/DropdownMenu";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { useMemo } from "react";

export interface ILanguageSwitcherProps {
    variant?: ButtonProps["variant"];
    triggerType?: "icon" | "text";
    buttonClassNames?: string;
    hideTriggerIcon?: bool;
    size?: ButtonProps["size"];
    contentAttrs?: Record<string, string>;
    onOpenChange?: (open: boolean) => void;
    asForm?: {
        initialValue: string;
        disabled?: bool;
        includeCurrent?: bool;
        onChange: (lang: string) => void;
    };
}

function LanguageSwitcher({
    variant,
    triggerType,
    buttonClassNames,
    hideTriggerIcon,
    size = "default",
    contentAttrs,
    onOpenChange,
    asForm,
}: ILanguageSwitcherProps): React.JSX.Element {
    const [t, i18n] = useTranslation();
    const curLang = useMemo(() => (asForm ? asForm.initialValue : i18n.language), [asForm, asForm?.initialValue, i18n, i18n.language]);

    const changeLanguageHandler = (lang: string) => {
        if (asForm) {
            asForm.onChange(lang);
            return;
        }

        if (!LANGUAGE_LOCALES.includes(lang)) {
            return;
        }

        i18n.changeLanguage(lang);
    };

    const langs = LANGUAGE_LOCALES.filter((locale) => asForm?.includeCurrent || locale !== curLang);

    return (
        <DropdownMenu.Root onOpenChange={onOpenChange}>
            <DropdownMenu.Trigger asChild>
                <Button
                    variant={variant ?? "default"}
                    className={cn("inline-flex", buttonClassNames)}
                    title={t("locales.title")}
                    size={size}
                    disabled={asForm?.disabled}
                >
                    {triggerType === "text" ? t(`locales.${curLang}`) : <IconComponent icon={`country-${curLang.split("-").pop()}`} />}
                    {hideTriggerIcon ? null : <IconComponent icon="chevron-down" size="4" className="ml-3" />}
                </Button>
            </DropdownMenu.Trigger>

            {langs.length === 0 ? null : (
                <DropdownMenu.Content
                    {...contentAttrs}
                    align="end"
                    className="min-w-44 max-w-[calc(100vw-1rem)] rounded-2xl border-border/70 p-1.5 shadow-xl shadow-black/20"
                >
                    <DropdownMenu.RadioGroup value={curLang} onValueChange={changeLanguageHandler}>
                        {langs.map((locale) => {
                            return (
                                <DropdownMenu.RadioItem
                                    value={locale}
                                    key={locale}
                                    className="min-h-10 gap-3 rounded-xl py-2 pl-8 pr-3 data-[state=checked]:bg-accent/60 data-[state=checked]:font-medium"
                                >
                                    <IconComponent icon={`country-${locale.split("-").pop()}`} className="shrink-0" />
                                    {t(`locales.${locale}`)}
                                </DropdownMenu.RadioItem>
                            );
                        })}
                    </DropdownMenu.RadioGroup>
                </DropdownMenu.Content>
            )}
        </DropdownMenu.Root>
    );
}

export default LanguageSwitcher;
