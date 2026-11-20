import { createRoot } from "react-dom/client";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TCardPriority } from "@langboard/core/constants";
import { getPriorityChoices } from "@/core/constants/PriorityChoiceConfig";
import SteppedChoiceSlider from "./SteppedChoiceSlider";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

const params = new URLSearchParams(location.search);
function Fixture() {
    const { t } = useTranslation();
    const [value, setValue] = useState<TCardPriority>("P2");
    const [commits, setCommits] = useState<TCardPriority[]>([]);
    const choices = getPriorityChoices(t);
    return (
        <main style={{ width: "min(100%, 420px)", padding: 16 }}>
            <SteppedChoiceSlider
                choices={params.has("no-effect") ? choices.map(({ effect: _, ...choice }) => choice) : choices}
                value={value}
                onChange={setValue}
                onCommit={(next) => setCommits((previous) => [...previous, next])}
                disabled={params.has("disabled")}
                ariaLabel={t("priority.name")}
            />
            <button onClick={() => setValue("P2")}>External reset</button>
            <output data-testid="value">{value}</output>
            <output data-testid="commits">{JSON.stringify(commits)}</output>
        </main>
    );
}
void i18n.changeLanguage(params.get("lang") ?? "en-US").then(() => createRoot(document.getElementById("root")!).render(<Fixture />));
