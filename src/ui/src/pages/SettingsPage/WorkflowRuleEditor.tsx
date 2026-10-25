import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import type { TFunction } from "i18next";
import { CARD_CATEGORIZED_EVENT_TYPES, DomainEventType } from "@/core/events/DomainEventType";
import {
    IWorkflowBotOptions,
    IWorkflowRule,
    TActionKind,
    TConditionKind,
    TNumericConditionKind,
    TWorkflowAction,
    TWorkflowCondition,
    TWorkflowTrigger,
} from "@/controllers/api/settings/workflowStages/useWorkflowStages";

const workflowEventTypes = Object.values(DomainEventType).filter((event) => event.startsWith("card_") && event !== DomainEventType.CardDeleted);

export const workflowConditionLabels: Record<TConditionKind, string> = {
    stage_residency_seconds: "Stage residency seconds",
    uncompacted_characters: "Compact after characters",
    uncompacted_comments: "Compact after comments",
    uncompacted_turns: "Compact after conversation turns",
    actor_is_not_author: "Actor is not the author",
    has_unfinished_items: "Has unfinished items",
    timers_stopped: "Timers stopped",
    approvals_clear: "Approvals clear",
    dependencies_clear: "Dependencies clear",
};
export const workflowActionLabels: Record<TActionKind, string> = {
    "bot.invoke": "Invoke bot",
    "comments.compact": "Compact comments",
    "card.archive": "Auto archive cards",
    "checkitems.complete": "Complete unchecked items",
    "timers.stop": "Stop running timers",
    "author.notify": "Notify the author when someone else enters this stage",
};
export const workflowEventLabel = (event: string, t: TFunction) => {
    if (event === "stage_entered") return t("settings.Stage entered");
    if (event === "interval") return t("settings.Interval elapsed");
    const category =
        event === DomainEventType.CardCreated
            ? "card"
            : Object.entries(CARD_CATEGORIZED_EVENT_TYPES).find(([, events]) => events.includes(event as DomainEventType))?.[0];
    if (!category) return event;
    return t("settings.Workflow event label", {
        category: t(`botTriggerCondition.${category}.Category`),
        event: t(`botTriggerCondition.${category}.conditions.${event}`),
    });
};
const numeric = (kind: TConditionKind): kind is TNumericConditionKind => kind === "stage_residency_seconds" || kind.startsWith("uncompacted_");
const compact = (kind: TConditionKind) => kind.startsWith("uncompacted_");
const newRule = (): IWorkflowRule => ({
    name: "",
    is_enabled: true,
    trigger: { kind: "stage_entered" },
    condition_match: "all",
    conditions: [],
    actions: [],
});
const newAction = (kind: TActionKind, previous: boolean): TWorkflowAction => {
    const base = { requires_previous_success: previous };
    if (kind === "bot.invoke") return { ...base, kind, executor: { kind: "bot", uid: "" }, config: { bot_uid: "" } };
    if (kind === "card.archive") return { ...base, kind, executor: { kind: "compactor" }, config: { archive_after_seconds: 86400 } };
    if (kind === "comments.compact") return { ...base, kind, executor: { kind: "compactor" }, config: {} };
    return { ...base, kind, executor: { kind: "event_actor" }, config: {} };
};
// Reordering and removal must preserve the predecessor safety invariant.
const orderActions = (actions: TWorkflowAction[]) =>
    actions.map((action, index) => ({
        ...action,
        requires_previous_success:
            index > 0 && (action.requires_previous_success || actions.slice(0, index).some((item) => item.kind === "comments.compact")),
    }));

export const workflowRulesValid = (rules: IWorkflowRule[]) =>
    rules.every((rule) => {
        const hasCompaction = rule.actions.some((action) => action.kind === "comments.compact");
        return (
            rule.name.trim().length > 0 &&
            rule.actions.length > 0 &&
            (rule.trigger.kind !== "event" || workflowEventTypes.includes(rule.trigger.event_type)) &&
            (rule.trigger.kind !== "interval" ||
                (Number.isInteger(rule.trigger.interval_seconds) && rule.trigger.interval_seconds >= 60 && rule.trigger.interval_seconds <= 86400)) &&
            rule.conditions.every((condition) =>
                condition.operator === "gte" ? Number.isInteger(condition.value) && condition.value > 0 : typeof condition.value === "boolean"
            ) &&
            (!hasCompaction ||
                (rule.condition_match === "any" && rule.conditions.length > 0 && rule.conditions.every((condition) => compact(condition.kind))))
        );
    });

export default function WorkflowRuleEditor({
    rules,
    onChange,
    disabled,
    botOptions,
    botOptionsError,
    canReadBots,
}: {
    rules: IWorkflowRule[];
    onChange: (rules: IWorkflowRule[]) => void;
    disabled: boolean;
    botOptions: IWorkflowBotOptions;
    botOptionsError: boolean;
    canReadBots: boolean;
}) {
    const [t] = useTranslation();
    const label = (value: string) => t(`settings.${value}`);
    const update = (index: number, fields: Partial<IWorkflowRule>) => onChange(rules.map((rule, i) => (i === index ? { ...rule, ...fields } : rule)));
    const move = <T,>(items: T[], index: number, direction: number) => {
        const next = [...items];
        [next[index], next[index + direction]] = [next[index + direction], next[index]];
        return next;
    };
    return (
        <fieldset disabled={disabled} className="space-y-4 rounded-xl border p-3">
            <legend className="px-1 text-sm font-medium">{label("Workflow rules")}</legend>
            <p className="text-xs text-muted-foreground">{label("Saving does not replay effects on existing cards.")}</p>
            {!workflowRulesValid(rules) && (
                <p role="alert" className="text-sm">
                    {label("Each rule requires a name, an action and valid conditions. Compaction requires at least one positive OR threshold.")}
                </p>
            )}
            {botOptionsError && (
                <p role="status" className="text-xs">
                    {label("Bot options unavailable. Saved bindings are preserved.")}
                </p>
            )}
            {rules.map((rule, ri) => {
                const hasCompaction = rule.actions.some((action) => action.kind === "comments.compact");
                return (
                    <fieldset key={rule.uid ?? ri} className="min-w-0 space-y-3 rounded-md border p-3" aria-label={`${label("Rule")} ${ri + 1}`}>
                        <legend className="px-1 text-sm font-medium">
                            {label("Rule")} {ri + 1}
                        </legend>
                        <label className="block space-y-1 text-sm">
                            {label("Trigger")}
                            <select
                                className="select w-full rounded-md border bg-background p-2"
                                aria-label={label("Trigger")}
                                value={rule.trigger.kind}
                                onChange={(event) => {
                                    const kind = event.target.value as TWorkflowTrigger["kind"];
                                    const trigger: TWorkflowTrigger =
                                        kind === "event"
                                            ? { kind, event_type: DomainEventType.CardCommentAdded }
                                            : kind === "interval"
                                              ? { kind, interval_seconds: 300 }
                                              : { kind };
                                    update(ri, { trigger });
                                }}
                            >
                                <option value="stage_entered">{label("Stage entered")}</option>
                                <option value="event">{label("Event occurred")}</option>
                                <option value="interval">{label("Interval elapsed")}</option>
                            </select>
                        </label>
                        {rule.trigger.kind === "event" && (
                            <label className="block space-y-1 text-sm">
                                {label("Event type")}
                                <select
                                    className="select w-full rounded-md border bg-background p-2"
                                    aria-label={label("Event type")}
                                    value={rule.trigger.event_type}
                                    onChange={(event) =>
                                        update(ri, { trigger: { kind: "event", event_type: event.target.value as DomainEventType } })
                                    }
                                >
                                    {workflowEventTypes.map((value) => (
                                        <option key={value} value={value}>
                                            {workflowEventLabel(value, t)}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        )}
                        {rule.trigger.kind === "interval" && (
                            <label className="block space-y-1 text-sm">
                                {label("Rule interval minutes")}
                                <Input
                                    type="number"
                                    min={1}
                                    max={1440}
                                    step="any"
                                    required
                                    value={rule.trigger.interval_seconds / 60}
                                    onChange={(event) =>
                                        update(ri, { trigger: { kind: "interval", interval_seconds: Math.round(Number(event.target.value) * 60) } })
                                    }
                                />
                            </label>
                        )}
                        <label className="block space-y-1 text-sm">
                            {label("Rule name")}
                            <Input required maxLength={100} value={rule.name} onChange={(event) => update(ri, { name: event.target.value })} />
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="checkbox" checked={rule.is_enabled} onChange={(event) => update(ri, { is_enabled: event.target.checked })} />
                            {label("Rule enabled")}
                        </label>
                        <label className="block space-y-1 text-sm">
                            {label("Condition match")}
                            <select
                                className="select w-full rounded-md border bg-background p-2"
                                aria-label={label("Condition match")}
                                value={rule.condition_match}
                                disabled={hasCompaction}
                                onChange={(event) => update(ri, { condition_match: event.target.value as "all" | "any" })}
                            >
                                <option value="all">{label("All conditions")}</option>
                                <option value="any">{label("Any condition")}</option>
                            </select>
                        </label>
                        {rule.conditions.map((condition, ci) => (
                            <div key={ci} className="space-y-2 rounded-md border p-2">
                                <label className="block space-y-1 text-sm">
                                    {label("Condition type")} {ci + 1}
                                    <select
                                        className="select w-full rounded-md border bg-background p-2"
                                        aria-label={`${label("Condition type")} ${ci + 1}`}
                                        value={condition.kind}
                                        onChange={(event) => {
                                            const kind = event.target.value as TConditionKind;
                                            const next: TWorkflowCondition = numeric(kind)
                                                ? { kind, operator: "gte", value: 1 }
                                                : { kind, operator: "eq", value: true };
                                            update(ri, { conditions: rule.conditions.map((item, i) => (i === ci ? next : item)) });
                                        }}
                                    >
                                        {Object.entries(workflowConditionLabels).map(([kind, text]) => (
                                            <option key={kind} value={kind} disabled={hasCompaction && !compact(kind as TConditionKind)}>
                                                {label(text)}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block space-y-1 text-sm">
                                    {label("Condition value")} {ci + 1}
                                    {condition.operator === "gte" ? (
                                        <Input
                                            type="number"
                                            min={1}
                                            step={1}
                                            required
                                            value={condition.value}
                                            onChange={(event) =>
                                                update(ri, {
                                                    conditions: rule.conditions.map((item, i) =>
                                                        i === ci ? { ...condition, value: Number(event.target.value) } : item
                                                    ),
                                                })
                                            }
                                        />
                                    ) : (
                                        <select
                                            className="select w-full rounded-md border bg-background p-2"
                                            aria-label={`${label("Condition value")} ${ci + 1}`}
                                            value={String(condition.value)}
                                            onChange={(event) =>
                                                update(ri, {
                                                    conditions: rule.conditions.map((item, i) =>
                                                        i === ci ? { ...condition, value: event.target.value === "true" } : item
                                                    ),
                                                })
                                            }
                                        >
                                            <option value="true">{label("Yes")}</option>
                                            <option value="false">{label("No")}</option>
                                        </select>
                                    )}
                                </label>
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={() => update(ri, { conditions: rule.conditions.filter((_, i) => i !== ci) })}
                                >
                                    {label("Remove condition")}
                                </Button>
                            </div>
                        ))}
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() =>
                                update(ri, {
                                    conditions: [
                                        ...rule.conditions,
                                        hasCompaction
                                            ? { kind: "uncompacted_characters", operator: "gte", value: 12000 }
                                            : { kind: "actor_is_not_author", operator: "eq", value: true },
                                    ],
                                })
                            }
                        >
                            {label("Add condition")}
                        </Button>
                        {hasCompaction && (
                            <div className="space-y-2 text-xs text-muted-foreground">
                                <p>
                                    {label(
                                        "Compaction thresholds use OR. Remove a threshold to disable it; " +
                                            "at least one positive threshold is required. Unrelated conditions belong in a separate rule."
                                    )}
                                </p>
                                <p>
                                    {label(
                                        "A conversation turn is a consecutive run of comments by the same actor in creation order. " +
                                            "Activity events and previous summaries do not count."
                                    )}
                                </p>
                            </div>
                        )}
                        {rule.actions.map((action, ai) => {
                            const priorCompact = rule.actions.slice(0, ai).some((item) => item.kind === "comments.compact");
                            const botUID = action.kind === "bot.invoke" ? action.config.bot_uid : "";
                            return (
                                <fieldset
                                    key={action.uid ?? ai}
                                    className="min-w-0 space-y-2 rounded-md border p-2"
                                    aria-label={`${label("Action")} ${ai + 1}`}
                                >
                                    <legend className="px-1 text-sm">
                                        {label("Action")} {ai + 1}
                                    </legend>
                                    <p className="text-sm">{label(workflowActionLabels[action.kind])}</p>
                                    <p className="text-xs text-muted-foreground">
                                        {label("Executor")}:{" "}
                                        {label(
                                            action.executor.kind === "compactor"
                                                ? "Dedicated compactor"
                                                : action.executor.kind === "bot"
                                                  ? "Bot"
                                                  : "Event actor"
                                        )}
                                    </p>
                                    {action.executor.kind === "compactor" && (
                                        <p className="text-xs text-muted-foreground">
                                            {label(
                                                "Uses the currently configured dedicated compactor profile and an explicit current board grant. " +
                                                    "Linking a rule grants no permission."
                                            )}
                                        </p>
                                    )}
                                    {action.kind === "bot.invoke" && (
                                        <>
                                            <p className="text-xs text-muted-foreground">
                                                {label(
                                                    "Native bot conditions, scope, active state and frozen state remain independent " +
                                                        "eligibility checks. Interval actions also require an available card schedule."
                                                )}
                                            </p>
                                            <label className="block space-y-1 text-sm">
                                                {label("Bot")}
                                                <select
                                                    className="select w-full rounded-md border bg-background p-2"
                                                    aria-label={label("Bot")}
                                                    value={botUID}
                                                    required
                                                    disabled={!canReadBots || botOptionsError}
                                                    onChange={(event) => {
                                                        const uid = event.target.value;
                                                        const schedule = botOptions.schedules.find(
                                                            (item) => item.bot_uid === uid && item.is_available
                                                        );
                                                        update(ri, {
                                                            actions: rule.actions.map((item, i) =>
                                                                i === ai
                                                                    ? {
                                                                          ...action,
                                                                          executor: { kind: "bot", uid },
                                                                          config: {
                                                                              bot_uid: uid,
                                                                              ...(rule.trigger.kind === "interval" && schedule
                                                                                  ? { schedule_uid: schedule.uid }
                                                                                  : {}),
                                                                          },
                                                                      }
                                                                    : item
                                                            ),
                                                        });
                                                    }}
                                                >
                                                    <option value="" disabled>
                                                        {label("Select bot")}
                                                    </option>
                                                    {botUID && !botOptions.bots.some((bot) => bot.uid === botUID) && (
                                                        <option value={botUID} disabled>
                                                            {botUID} ({label("Unavailable")})
                                                        </option>
                                                    )}
                                                    {botOptions.bots.map((bot) => {
                                                        const available =
                                                            rule.trigger.kind === "interval"
                                                                ? botOptions.schedules.some((item) => item.bot_uid === bot.uid && item.is_available)
                                                                : rule.trigger.kind === "stage_entered"
                                                                  ? bot.entry_available
                                                                  : bot.policy_available;
                                                        return (
                                                            <option key={bot.uid} value={bot.uid} disabled={!available}>
                                                                {bot.name}
                                                                {available ? "" : ` (${label("Unavailable")})`}
                                                            </option>
                                                        );
                                                    })}
                                                </select>
                                            </label>
                                            {rule.trigger.kind === "interval" && (
                                                <label className="block space-y-1 text-sm">
                                                    {label("Card schedule")}
                                                    <select
                                                        className="select w-full rounded-md border bg-background p-2"
                                                        required
                                                        aria-label={label("Card schedule")}
                                                        value={action.config.schedule_uid ?? ""}
                                                        disabled={!canReadBots || botOptionsError}
                                                        onChange={(event) =>
                                                            update(ri, {
                                                                actions: rule.actions.map((item, i) =>
                                                                    i === ai
                                                                        ? {
                                                                              ...action,
                                                                              config: { ...action.config, schedule_uid: event.target.value },
                                                                          }
                                                                        : item
                                                                ),
                                                            })
                                                        }
                                                    >
                                                        <option value="" disabled>
                                                            {label("Select an active card schedule")}
                                                        </option>
                                                        {action.config.schedule_uid &&
                                                            !botOptions.schedules.some(
                                                                (schedule) =>
                                                                    schedule.uid === action.config.schedule_uid && schedule.bot_uid === botUID
                                                            ) && (
                                                                <option value={action.config.schedule_uid} disabled>
                                                                    {action.config.schedule_uid} ({label("Unavailable")})
                                                                </option>
                                                            )}
                                                        {botOptions.schedules
                                                            .filter((schedule) => schedule.bot_uid === botUID)
                                                            .map((schedule) => (
                                                                <option key={schedule.uid} value={schedule.uid} disabled={!schedule.is_available}>
                                                                    {schedule.name}
                                                                    {schedule.is_available ? "" : ` (${label("Unavailable")})`}
                                                                </option>
                                                            ))}
                                                    </select>
                                                </label>
                                            )}
                                        </>
                                    )}
                                    {action.kind === "card.archive" && (
                                        <label className="block space-y-1 text-sm">
                                            {label("Archive after hours")}
                                            <Input
                                                type="number"
                                                min={0}
                                                max={8760}
                                                step="any"
                                                required
                                                value={action.config.archive_after_seconds / 3600}
                                                onChange={(event) =>
                                                    update(ri, {
                                                        actions: rule.actions.map((item, i) =>
                                                            i === ai
                                                                ? {
                                                                      ...action,
                                                                      config: {
                                                                          archive_after_seconds: Math.round(Number(event.target.value) * 3600),
                                                                      },
                                                                  }
                                                                : item
                                                        ),
                                                    })
                                                }
                                            />
                                        </label>
                                    )}
                                    <label className="flex items-center gap-2 text-sm">
                                        <input
                                            type="checkbox"
                                            checked={action.requires_previous_success}
                                            disabled={ai === 0 || priorCompact}
                                            onChange={(event) =>
                                                update(ri, {
                                                    actions: rule.actions.map((item, i) =>
                                                        i === ai ? { ...item, requires_previous_success: event.target.checked } : item
                                                    ),
                                                })
                                            }
                                        />
                                        {label("Require previous action success")}
                                    </label>
                                    <div className="flex flex-wrap gap-2">
                                        <Button
                                            type="button"
                                            variant="outline"
                                            disabled={ai === 0}
                                            onClick={() => update(ri, { actions: orderActions(move(rule.actions, ai, -1)) })}
                                        >
                                            {label("Move action up")}
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="outline"
                                            disabled={ai === rule.actions.length - 1}
                                            onClick={() => update(ri, { actions: orderActions(move(rule.actions, ai, 1)) })}
                                        >
                                            {label("Move action down")}
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="outline"
                                            onClick={() => update(ri, { actions: orderActions(rule.actions.filter((_, i) => i !== ai)) })}
                                        >
                                            {label("Remove action")}
                                        </Button>
                                    </div>
                                </fieldset>
                            );
                        })}
                        <label className="block space-y-1 text-sm">
                            {label("Add action")}
                            <select
                                className="select w-full rounded-md border bg-background p-2"
                                aria-label={label("Add action")}
                                value=""
                                onChange={(event) => {
                                    const kind = event.target.value as TActionKind;
                                    if (!kind) return;
                                    update(ri, {
                                        actions: [...rule.actions, newAction(kind, rule.actions.length > 0 && hasCompaction)],
                                        ...(kind === "comments.compact"
                                            ? {
                                                  condition_match: "any",
                                                  conditions: rule.conditions.filter((item) => compact(item.kind)).length
                                                      ? rule.conditions.filter((item) => compact(item.kind))
                                                      : [{ kind: "uncompacted_characters", operator: "gte", value: 12000 }],
                                              }
                                            : {}),
                                    });
                                }}
                            >
                                <option value="">{label("Select action")}</option>
                                {Object.entries(workflowActionLabels).map(([kind, text]) => (
                                    <option
                                        key={kind}
                                        value={kind}
                                        disabled={
                                            (kind === "bot.invoke" && (!canReadBots || botOptionsError)) ||
                                            (kind === "comments.compact" && rule.conditions.some((condition) => !compact(condition.kind)))
                                        }
                                    >
                                        {label(text)}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <Button type="button" variant="outline" onClick={() => onChange(rules.filter((_, i) => i !== ri))}>
                            {label("Remove rule")}
                        </Button>
                    </fieldset>
                );
            })}
            <Button type="button" variant="outline" onClick={() => onChange([...rules, newRule()])}>
                {label("Add rule")}
            </Button>
        </fieldset>
    );
}
