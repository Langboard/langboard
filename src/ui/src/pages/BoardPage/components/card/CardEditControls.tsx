import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { useTranslation } from "react-i18next";

export interface ICardEditControlsProps {
    className?: string;
    canEdit?: bool;
    isEditing: bool;
    onEdit?: () => void;
    onSave: () => void;
    onCancel: () => void;
    saveDisabled?: bool;
}

export const CardEditControls = ({
    className,
    canEdit = false,
    isEditing,
    onEdit,
    onSave,
    onCancel,
    saveDisabled = false,
}: ICardEditControlsProps): React.JSX.Element | null => {
    const [t] = useTranslation();

    if (!isEditing && (!canEdit || !onEdit)) {
        return null;
    }

    return (
        <Flex className={cn("gap-2 px-4 py-2 transition-colors duration-150 motion-reduce:transition-none", className)} justify="end" items="center">
            <Flex className="gap-2">
                {isEditing ? (
                    <>
                        <Button variant="outline" size="sm" onClick={onCancel} className="gap-1">
                            <IconComponent icon="x" size="4" />
                            {t("common.Cancel")}
                        </Button>

                        <Button variant="default" size="sm" onClick={onSave} disabled={saveDisabled} className="gap-1">
                            <IconComponent icon="save" size="4" />
                            {t("common.Save")}
                        </Button>
                    </>
                ) : (
                    <Button variant="outline" size="sm" onClick={onEdit} className="gap-1">
                        <IconComponent icon="pen" size="4" />
                        {t("common.Edit")}
                    </Button>
                )}
            </Flex>
        </Flex>
    );
};

export default CardEditControls;
