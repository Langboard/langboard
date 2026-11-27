import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import WikiCreateButton from "@/pages/BoardPage/components/wiki/WikiCreateButton";
import { useTranslation } from "react-i18next";

const WikiEmptyState = () => {
    const [t] = useTranslation();

    return (
        <Flex
            direction="col"
            items="center"
            justify="center"
            className="min-h-[18rem] gap-4 rounded-lg border border-dashed bg-muted/30 p-8 text-center"
        >
            <Flex
                items="center"
                justify="center"
                className="size-12 rounded-full border border-dashed border-muted-foreground/40 bg-background text-muted-foreground"
            >
                <IconComponent icon="file-plus" size="6" />
            </Flex>
            <Box className="space-y-1">
                <p className="text-base font-semibold text-foreground">{t("wiki.Start your first wiki")}</p>
                <p className="text-sm text-muted-foreground">{t("wiki.Add your first wiki page to keep everything in one place.")}</p>
            </Box>
            <WikiCreateButton />
        </Flex>
    );
};

export default WikiEmptyState;
