import { NotificationScheduleRuleModel } from "@/core/models";

export type TNotificationRuleValue = Pick<
    NotificationScheduleRuleModel.Interface,
    "name" | "is_enabled" | "interval_str" | "target" | "field" | "operator" | "value" | "recipients" | "repeat_after_hours"
>;

export function getNotificationScheduleTimezone() {
    return new Date().getTimezoneOffset() / -60;
}

export function normalizeNotificationRule(
    rule: TNotificationRuleValue,
    schema: NotificationScheduleRuleModel.IRuleSchema | null
): TNotificationRuleValue {
    const targetSchema = schema?.targets.find((target) => target.key === rule.target);
    const fieldSchema = targetSchema?.fields.find((field) => field.key === rule.field) || targetSchema?.fields[0];
    const operator = fieldSchema?.operators.includes(rule.operator) ? rule.operator : fieldSchema?.operators[0] || rule.operator;
    const operatorSchema = operator ? schema?.operators[operator] : null;
    const valueKey = `${rule.target}.${fieldSchema?.key || rule.field}`;
    const dynamicValues = schema?.values[valueKey] || [];
    let value = rule.value;

    if (operatorSchema?.value_type === "none") {
        value = null;
    } else if (operatorSchema?.value_type === "dynamic" && dynamicValues.length && !dynamicValues.includes(value as never)) {
        value = dynamicValues[0];
    } else if (operatorSchema?.value_type === "number") {
        value = Math.max(Number(value) || 0, operatorSchema.min || 0);
    }

    return {
        ...rule,
        field: fieldSchema?.key || rule.field,
        operator,
        value,
        interval_str: rule.interval_str || "0 9 * * *",
        recipients: rule.recipients.filter((recipient) => targetSchema?.recipients.includes(recipient)),
        repeat_after_hours: Math.max(Number(rule.repeat_after_hours) || 24, 1),
    };
}

export function parseNotificationRuleValue(value: string) {
    if (value === "true") {
        return true;
    }
    if (value === "false") {
        return false;
    }

    return value;
}
