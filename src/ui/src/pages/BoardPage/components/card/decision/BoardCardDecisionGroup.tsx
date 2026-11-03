import Button from "@/components/base/Button";
import { useTranslation } from "react-i18next";
import { useDecisions } from "./DecisionContext";
import DecisionGate from "./DecisionGate";
import DecisionView, { DecisionStatus } from "./DecisionView";

export default function BoardCardDecisionGroup() {
    const [t] = useTranslation();
    const { page, locked, next, previous, hasPrevious, reload } = useDecisions();
    // Zero decisions add no section, empty state, badge, button or tab to the card.
    if (!page?.items.length && !hasPrevious) return null;
    return (
        <section aria-label={t("card.decision.Decisions")} className="min-w-0" data-card-outline-section="decisions">
            <details>
                <summary className="font-semibold">
                    {t("card.decision.Decisions")} ({page?.items.length ?? 0})
                </summary>
                <div className="space-y-3 py-3">
                    <DecisionStatus />
                    <Button size="sm" disabled={locked} onClick={reload}>
                        {t("card.decision.Refresh")}
                    </Button>
                    {page?.items.map((decision) => (
                        <DecisionView key={decision.uid} decision={decision} />
                    ))}
                    <DecisionGate />
                    <nav aria-label={t("card.decision.Decision pages")} className="flex flex-wrap gap-2">
                        <Button size="sm" disabled={locked || !hasPrevious} onClick={previous}>
                            {t("card.decision.Previous")}
                        </Button>
                        <Button size="sm" disabled={locked || !page?.next_cursor} onClick={next}>
                            {t("card.decision.Next")}
                        </Button>
                    </nav>
                </div>
            </details>
        </section>
    );
}
