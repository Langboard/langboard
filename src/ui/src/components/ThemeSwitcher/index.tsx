import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";
import Button, { type ButtonProps } from "@/components/base/Button";
import DropdownMenu from "@/components/base/DropdownMenu";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";

export interface IThemeSwitcherProps {
    variant?: ButtonProps["variant"];
    triggerType?: "icon" | "text";
    buttonClassNames?: string;
    hideTriggerIcon?: bool;
    size?: ButtonProps["size"];
}

const themes: Record<string, string> = {
    dark: "moon",
    light: "sun",
    system: "contrast",
};

function ThemeSwitcher({ variant, triggerType, buttonClassNames, hideTriggerIcon, size = "default" }: IThemeSwitcherProps): React.JSX.Element {
    const { theme, setTheme } = useTheme();
    const [t] = useTranslation();

    return (
        <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
                <Button
                    variant={variant ?? "default"}
                    className={cn("inline-flex", buttonClassNames)}
                    title={t("themes.title")}
                    aria-label={t("themes.title")}
                    size={size}
                >
                    {triggerType === "text" ? t(`themes.${theme ?? "system"}`) : <IconComponent icon={themes[theme ?? "system"]} />}
                    {hideTriggerIcon ? null : <IconComponent icon="chevron-down" size="4" className="ml-3" />}
                </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content>
                {Object.keys(themes).map((mode) => {
                    return (
                        <DropdownMenu.Item onClick={() => setTheme(mode.toLowerCase())} key={mode}>
                            <IconComponent icon={themes[mode]} className="mr-2" />
                            {t(`themes.${mode}`)}
                        </DropdownMenu.Item>
                    );
                })}
            </DropdownMenu.Content>
        </DropdownMenu.Root>
    );
}

export default ThemeSwitcher;
