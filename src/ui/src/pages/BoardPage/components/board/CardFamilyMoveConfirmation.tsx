import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter } from "@/components/plate-ui/alert-dialog";

export default function CardFamilyMoveConfirmation({ open, onDecision }: { open: boolean; onDecision: (value: boolean | null) => void }) {
    const [t] = useTranslation();
    return (
        <AlertDialog open={open} onOpenChange={(value) => !value && onDecision(null)}>
            <AlertDialogContent onEscapeKeyDown={() => onDecision(null)}>
                <AlertDialogTitle>{t("board.Move child cards too?")}</AlertDialogTitle>
                <AlertDialogDescription>
                    {t("board.All descendants will move to the same column. Relationships will be preserved.")}
                </AlertDialogDescription>
                <AlertDialogFooter>
                    <Button variant="outline" onClick={() => onDecision(null)}>
                        {t("common.Cancel")}
                    </Button>
                    <Button variant="secondary" onClick={() => onDecision(false)}>
                        {t("board.Move only this card")}
                    </Button>
                    <Button onClick={() => onDecision(true)}>{t("board.Move all descendants")}</Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );
}
