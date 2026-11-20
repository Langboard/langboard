import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createInstance } from "i18next";
import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import type { IAdmission } from "@/core/models/botSchedules/BaseBotScheduleModel";
import { scheduleAdmissionReason, scheduleAdmissionStatus } from "@/core/utils/ScheduleAdmissionCopy";
import BotScheduleAdmission from "./Admission";

test("all admission states render in four locales without claiming execution completion or offering an unsafe replay", async () => {
    for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
        const bot = JSON.parse(readFileSync(`src/assets/locales/${locale}/bot.json`, "utf8"));
        const i18n = createInstance();
        await i18n.init({ lng: locale, resources: { [locale]: { translation: { bot } } } });
        const render = (admission?: IAdmission) =>
            renderToStaticMarkup(
                <I18nextProvider i18n={i18n}>
                    <BotScheduleAdmission admission={admission} />
                </I18nextProvider>
            );
        assert.equal(render(), "");
        for (const state of ["pending", "dispatching", "accepted", "failed", "uncertain", "cancelled"] as const) {
            const html = render({
                uid: "private-occurrence",
                state,
                attempt: 2,
                max_attempts: 6,
                next_attempt_at: "2026-10-24T00:00:00Z",
                expires_at: "2026-10-25T00:00:00Z",
                error_code: state === "failed" ? "retry_limit_exceeded" : null,
            });
            assert.ok(html.includes(bot.schedules.admission.states[state]));
            assert.match(html, /2 \/ 6/);
            assert.ok(html.includes(bot.schedules.admission["Admission is not completion"]));
            assert.doesNotMatch(html, /<button|private-occurrence|bot\.schedules\./);
            assert.equal(/datetime="2026-10-24/i.test(html), state === "pending");
            assert.equal(/datetime="2026-10-25/i.test(html), state === "pending" || state === "dispatching");
            assert.equal(html.includes(bot.schedules.admission.errors.retry_limit_exceeded), state === "failed");
            assert.doesNotMatch(html, /retry_limit_exceeded/);
        }
        const reasonCopy = bot.schedules.admission.errors;
        for (const code of Object.keys(reasonCopy)) {
            assert.equal(scheduleAdmissionReason(i18n.t, code), reasonCopy[code]);
        }
        assert.equal(scheduleAdmissionReason(i18n.t, "private-synthetic-detail"), reasonCopy.unknown_failure);
        assert.equal(scheduleAdmissionStatus(i18n.t, "uncertain"), bot.schedules.admission.states.uncertain);

        const previous = render({
            uid: "previous-occurrence",
            is_current_occurrence: false,
            state: "pending",
            attempt: 2,
            max_attempts: 6,
            next_attempt_at: "2026-10-24T00:00:00Z",
            expires_at: "2026-10-25T00:00:00Z",
            error_code: "retryable_failure",
        });
        assert.ok(previous.includes(bot.schedules.admission["Previous occurrence"]));
        assert.ok(!previous.includes(bot.schedules.admission.states.pending));
        assert.doesNotMatch(previous, /datetime=|<button|previous-occurrence/);

        const unknown = render({
            uid: "private-occurrence",
            state: "future-state" as IAdmission["state"],
            attempt: 1,
            max_attempts: 6,
            next_attempt_at: "invalid",
            expires_at: "invalid",
            error_code: null,
        });
        assert.ok(unknown.includes(bot.schedules.admission.states.unknown));
        const invalidTime = render({
            uid: "x",
            state: "pending",
            attempt: 1,
            max_attempts: 6,
            next_attempt_at: "invalid",
            expires_at: "invalid",
            error_code: null,
        });
        assert.doesNotMatch(invalidTime, /Invalid Date|datetime=/i);
    }
});
