import Button from "@/components/base/Button";
import { SteppedChoiceSlider } from "@/components/SteppedChoiceSlider";
import { api } from "@/core/helpers/Api";
import { Routing } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const MODES = ["ignore", "comments", "comments_and_moves"] as const;
type Mode = (typeof MODES)[number];
const readMode = (value: unknown): Mode => {
    if (!MODES.includes(value as Mode)) throw new Error("Invalid card notification mode");
    return value as Mode;
};

export default function CardActivitySettings({ projectUID, cardUID }: { projectUID: string; cardUID: string }) {
    const [t] = useTranslation();
    const [saved, setSaved] = useState<Mode | null>(null);
    const [draft, setDraft] = useState<Mode>("ignore");
    const [busy, setBusy] = useState(false);
    const [failed, setFailed] = useState(false);
    const [reload, setReload] = useState(0);
    const writing = useRef(false);
    const url = `${Utils.String.format(Routing.API.NOTIFICATION.SETTINGS.CARD, { uid: projectUID, card_uid: cardUID })}/activity`;
    useEffect(() => {
        const controller = new AbortController();
        setSaved(null);
        setFailed(false);
        api.get(url, { signal: controller.signal })
            .then(({ data }) => {
                if (controller.signal.aborted) return;
                const mode = readMode(data.mode);
                setSaved(mode);
                setDraft(mode);
            })
            .catch(() => {
                if (!controller.signal.aborted) setFailed(true);
            });
        return () => controller.abort();
    }, [url, reload]);
    const commit = async (mode: Mode) => {
        if (saved === null || writing.current || mode === saved) return;
        writing.current = true;
        setBusy(true);
        setFailed(false);
        try {
            const { data } = await api.put(url, { mode });
            const persisted = readMode(data.mode);
            setSaved(persisted);
            setDraft(persisted);
        } catch {
            setDraft(saved);
            setFailed(true);
        } finally {
            writing.current = false;
            setBusy(false);
        }
    };
    const labels = ["Ignore", "Comments only", "Comments and card moves"];
    const hints = [
        "Do not notify me of ordinary card activity.",
        "Notify me when a comment is added.",
        "Notify me when a comment is added or the card moves.",
    ];
    return (
        <section className="w-72 max-w-[calc(100vw-3rem)] space-y-3 border-b pb-3" aria-busy={busy || saved === null}>
            <p className="text-sm font-medium">{t("notification.settings.Card activity")}</p>
            <SteppedChoiceSlider
                choices={MODES.map((id, index) => ({
                    id,
                    label: t(`notification.settings.${labels[index]}`),
                    hint: t(`notification.settings.${hints[index]}`),
                    color: ["#64748b", "#8b5cf6", "#14b8a6"][index],
                }))}
                value={draft}
                onChange={setDraft}
                onCommit={commit}
                disabled={saved === null || busy || failed}
                ariaLabel={t("notification.settings.Card activity")}
            />
            {failed && (
                <div role="alert" className="space-y-2 text-sm text-destructive">
                    <p>{t("notification.settings.Unable to load or save card activity settings.")}</p>
                    <Button size="sm" variant="outline" onClick={() => setReload((value) => value + 1)}>
                        {t("notification.settings.Reload settings")}
                    </Button>
                </div>
            )}
            <p className="text-xs text-muted-foreground">{t("notification.settings.Mentions and assignments use your existing channel settings.")}</p>
        </section>
    );
}
