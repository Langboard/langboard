import Floating from "@/components/base/Floating";
import Select from "@/components/base/Select";
import { AVAILABLE_RUNNING_TYPES_BY_PLATFORM, EBotPlatform, EBotPlatformRunningType } from "@langboard/core/ai";
import { useTranslation } from "react-i18next";

export interface IBotPlatformRunningTypeSelectProps {
    state: [EBotPlatformRunningType | undefined, (value: EBotPlatformRunningType) => void | Promise<void>];
    platform?: EBotPlatform;
    isValidating?: bool;
    disabled?: bool;
}

function BotPlatformRunningTypeSelect({ state, platform, isValidating, disabled }: IBotPlatformRunningTypeSelectProps) {
    const [t] = useTranslation();
    const [platformRunningType, changePlatformRunningType] = state;
    const selectedPlatform = platform ?? EBotPlatform.Default;
    const selectedPlatformRunningTypes =
        AVAILABLE_RUNNING_TYPES_BY_PLATFORM[selectedPlatform] ?? AVAILABLE_RUNNING_TYPES_BY_PLATFORM[EBotPlatform.Default];
    const selectedPlatformRunningType =
        platformRunningType && selectedPlatformRunningTypes.includes(platformRunningType)
            ? platformRunningType
            : (selectedPlatformRunningTypes[0] ?? EBotPlatformRunningType.Default);

    return (
        <Floating.LabelSelect
            label={t("settings.Select a platform running type")}
            value={selectedPlatformRunningType}
            defaultValue={selectedPlatformRunningType.toString()}
            onValueChange={changePlatformRunningType as (value: string) => void}
            disabled={isValidating || disabled}
            required
            options={selectedPlatformRunningTypes.map((targetPlatformRunningType) => (
                <Select.Item value={targetPlatformRunningType.toString()} key={`bot-platform-running-type-select-${targetPlatformRunningType}`}>
                    {t(`bot.platformRunningTypes.${targetPlatformRunningType}`)}
                </Select.Item>
            ))}
        />
    );
}

export default BotPlatformRunningTypeSelect;
