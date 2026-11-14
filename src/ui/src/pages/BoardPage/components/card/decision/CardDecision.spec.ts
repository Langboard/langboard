import type { Decision } from "./DecisionContext";
import { expect, Page, test } from "@playwright/test";
import { readFileSync } from "node:fs";
const fixture = "/src/pages/BoardPage/components/card/decision/CardDecision.fixture.html";
const locales = ["en-US", "ko-KR", "ja-JP", "zh-CN", "en-US"];
const privateDecisionDetail = "PRIVATE decision diagnostic /srv/private/approval.json";
const uid = "Dec00000001";
const actor = "User0000001";
const other = "User0000002";
const round = "11111111-1111-4111-8111-111111111111";
const proposal = (): Decision => ({
    uid,
    round_id: round,
    source_kind: "proposal",
    source_ref: "Card0000001",
    question: "Approve the original plan?",
    revision: 0,
    target_revision: "target-revision",
    policy_revision: "policy-revision",
    participant_uids: [actor, other],
    policy: { op: "all", required: 0, children: [{ actor_uid: actor }, { actor_uid: other }] },
    state: "pending",
    deadline: null,
    active_responders: [actor],
    allowed_actions: ["respond"],
    blocked_reason: null,
    effect_result: null,
});
const hitl = (): Decision => ({
    ...proposal(),
    source_kind: "graph_approval",
    approval_uid: "Approval001",
    automatic_resume: false,
    question: "Approve native execution?",
});
const poll = (visible = false, closed = false): Decision => ({
    ...proposal(),
    source_kind: "poll",
    question: "Choose a release plan",
    deadline: "2030-10-24T10:00:00Z",
    allowed_actions: closed ? [] : ["respond"],
    state: closed ? "passed" : "pending",
    poll: {
        options: [
            { uid: "Opt00000001", key: "proceed", label: "Proceed with plan" },
            { uid: "Opt00000002", key: "defer", label: "Defer the plan" },
        ],
        settings: {
            participation_quorum: 1,
            participation_policy: "vote_only",
            winner_policy: "plurality",
            tie_policy: "needs_info",
            no_quorum_policy: "expired",
            no_winner_policy: "needs_info",
            needs_info_policy: "record",
            allow_change: true,
            tally_visibility: "after_vote",
        },
        phase: closed ? "closed" : "open",
        my_response: visible
            ? {
                  actor_uid: actor,
                  choice: "vote",
                  option_uid: "Opt00000001",
                  response_revision: 1,
                  response_order: 1,
                  responded_at: "2026-10-24T08:00:00Z",
              }
            : null,
        can_change: !closed,
        tally_visible: visible,
        tally: visible
            ? { participation_count: 1, vote_count: 1, abstain_count: 0, needs_info_count: 0, counts_by_option: { Opt00000001: 1, Opt00000002: 0 } }
            : null,
        responses: visible
            ? [
                  {
                      actor_uid: other,
                      choice: "needs_info",
                      option_uid: null,
                      response_revision: 1,
                      response_order: 2,
                      responded_at: "2026-10-24T08:10:00Z",
                  },
              ]
            : null,
        winner_option_uid: visible && closed ? "Opt00000001" : null,
        outcome_reason: closed ? "winner" : "poll_open",
    },
});
type Case = ReturnType<typeof proposal> | ReturnType<typeof poll>;
async function transport(
    page: Page,
    config: {
        initial?: Case[];
        ambiguous?: boolean;
        status?: number;
        pagination?: boolean;
        group?: boolean;
        previewStatus?: number;
        previewDelay?: number;
        gateInitial?: Record<string, string>[];
        gateStatus?: number;
        gateReadStatus?: number;
        gateAmbiguous?: boolean;
        gateDelay?: number;
        approvalDelay?: number;
        noApprovals?: boolean;
        readFailure?: "network" | 503;
    } = {}
) {
    let cases = config.initial ?? [];
    const calls: { method: string; path: string; data: Record<string, unknown>; query: URLSearchParams }[] = [];
    let failed = false;
    let decisionReads = 0;
    let gates = config.gateInitial ?? [];
    let gateFailed = false;
    await page.route(/\/board\/Board000001\/graph-approvals(?:[/?]|$)/, async (route) => {
        const url = new URL(route.request().url());
        calls.push({ method: route.request().method(), path: url.pathname, data: {}, query: url.searchParams });
        if (config.approvalDelay) await new Promise((resolve) => setTimeout(resolve, config.approvalDelay));
        const approval = {
            uid: "Approval001",
            project_uid: "Board000001",
            scope_table: "card",
            scope_uid: "Card0000001",
            origin_type: "chat",
            status: "pending",
            action_type: "update",
            permission: "card_update",
            thread_id: "thread",
            run_id: "run",
            preview_payload: {},
            durable_run: true,
            decision_bound: false,
            created_at: "2026-10-24T08:00:00Z",
            updated_at: "2026-10-24T08:00:00Z",
        };
        return route.fulfill({
            json: {
                approvals: config.noApprovals
                    ? []
                    : [
                          approval,
                          { ...approval, uid: "Bound000001", decision_bound: true },
                          { ...approval, uid: "Unknown0001", decision_bound: undefined },
                          { ...approval, uid: "WrongCard01", scope_uid: "Card0000002" },
                          { ...approval, uid: "WrongBoard1", project_uid: "Board000002" },
                          { ...approval, uid: "WrongScope1", scope_table: "project", scope_uid: "Board000001" },
                          { ...approval, uid: "Resolved001", status: "resolved" },
                      ],
            },
        });
    });
    await page.route(/\/board\/Board000001\/(?:card\/Card000000[12]\/decision-gate|workflow-stages)(?:[/?]|$)/, async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const data = request.postDataJSON() ?? {};
        calls.push({ method: request.method(), path: url.pathname, data, query: url.searchParams });
        if (url.pathname.endsWith("workflow-stages"))
            return route.fulfill({
                json: {
                    stages: [
                        { key: "review", name: "Review", is_active: true },
                        { key: "retired", name: "Retired stage", is_active: false },
                    ],
                },
            });
        if (request.method() === "GET") {
            if (config.gateDelay) await new Promise((resolve) => setTimeout(resolve, config.gateDelay));
            if (config.gateReadStatus) return route.fulfill({ status: config.gateReadStatus, json: { detail: privateDecisionDetail } });
            return route.fulfill({ json: { gates: url.pathname.includes("Card0000002") ? [] : gates } });
        }
        if (config.gateStatus)
            return route.fulfill({
                status: config.gateStatus,
                json: { code: "decision_gate_blocked", reason: "round_changed", detail: privateDecisionDetail },
            });
        if (request.method() === "PUT") gates = [data];
        else gates = [];
        if (config.gateAmbiguous && !gateFailed) {
            gateFailed = true;
            return route.abort("failed");
        }
        return route.fulfill({ json: request.method() === "PUT" ? data : { removed: true } });
    });
    await page.route(
        new RegExp(
            "/board/Board000001/(?:card/Card000000[12]/(?:decisions|decision-actors/(?:candidates|preview))" +
                "|decisions/Dec00000001/responses)(?:[/?]|$)"
        ),
        async (route) => {
            const request = route.request();
            const url = new URL(request.url());
            const data = request.postDataJSON() ?? {};
            calls.push({ method: request.method(), path: url.pathname, data, query: url.searchParams });
            if (request.method() === "GET") {
                if (url.pathname.endsWith("/candidates"))
                    return route.fulfill({
                        json: {
                            source_kind: url.searchParams.get("source_kind"),
                            authorization: `${url.searchParams.get("source_kind")}_response_candidate`,
                            abilities: { select_user: true, select_team: true, select_group: config.group ?? true },
                            records:
                                url.searchParams.get("kind") === "user"
                                    ? [
                                          { kind: "user", uid: actor, label: "Ada Reader" },
                                          { kind: "user", uid: other, label: "Grace Reader" },
                                      ]
                                    : [
                                          {
                                              kind: url.searchParams.get("kind"),
                                              uid: url.searchParams.get("kind") === "group" ? "Group000001" : "Team0000001",
                                              label: url.searchParams.get("kind") === "group" ? "Review group" : "Review team",
                                          },
                                      ],
                            total_records: 16,
                        },
                    });
                decisionReads++;
                if (config.readFailure && [2, 3].includes(decisionReads)) {
                    if (config.readFailure === "network") return route.abort("failed");
                    return route.fulfill({ status: config.readFailure, json: { code: "unavailable" } });
                }
                return route.fulfill({
                    json: {
                        items: url.pathname.includes("Card0000002") ? [] : cases,
                        next_cursor: config.pagination && !url.searchParams.has("before") ? uid : null,
                        target_revision: "target-revision",
                    },
                });
            }
            if (url.pathname.endsWith("/preview")) {
                if (config.previewDelay) await new Promise((resolve) => setTimeout(resolve, config.previewDelay));
                if (config.previewStatus) return route.fulfill({ status: config.previewStatus, json: { code: "VA0000" } });
                const sources = data.sources as { kind: string; uid: string }[];
                const ids = sources.some((item) => item.kind !== "user")
                    ? [actor, other]
                    : [actor, other].filter((id) => sources.some((item) => item.uid === id));
                return route.fulfill({
                    json: {
                        snapshot_version: 1,
                        source_kind: data.source_kind,
                        authorization: `${data.source_kind}_response_candidate`,
                        project_uid: "Board000001",
                        card_uid: url.pathname.includes("Card0000002") ? "Card0000002" : "Card0000001",
                        actor_uids: ids,
                        actors: ids.map((id) => ({
                            uid: id,
                            firstname: id === actor ? "Ada" : "Grace",
                            lastname: "Reader",
                            username: id,
                            sources: sources.filter((item) => item.kind !== "user" || item.uid === id),
                        })),
                        sources: sources.map((item) => ({
                            ...item,
                            label:
                                item.kind === "group"
                                    ? "Review group"
                                    : item.kind === "team"
                                      ? "Review team"
                                      : item.uid === actor
                                        ? "Ada Reader"
                                        : "Grace Reader",
                            revision: "a".repeat(64),
                            membership_count: item.kind === "user" ? 1 : 3,
                            eligible_count: item.kind === "user" ? 1 : 2,
                            excluded_count: item.kind === "user" ? 0 : 1,
                            exclusions: { not_currently_eligible: item.kind === "user" ? 0 : 1 },
                        })),
                        eligible_count: ids.length,
                        excluded_count: sources.some((item) => item.kind !== "user") ? 1 : 0,
                        exclusions: { not_currently_eligible: sources.some((item) => item.kind !== "user") ? 1 : 0 },
                        membership_revision: "b".repeat(64),
                        policy_revision: "c".repeat(64),
                        snapshot: "d".repeat(64),
                        abilities: { select_user: true, select_group: config.group ?? true, select_team: true },
                        execution_authority: false,
                    },
                });
            }
            if (config.status) return route.fulfill({ status: config.status, json: { code: "VA0000", detail: privateDecisionDetail } });
            if (config.ambiguous && !failed) {
                failed = true;
                return route.abort("failed");
            }
            if (url.pathname.endsWith("/responses")) {
                if (cases[0]?.source_kind === "poll") cases = [{ ...poll(true), revision: 1 }];
                else cases = [{ ...(cases[0] ?? proposal()), revision: 1, state: "passed", allowed_actions: [], active_responders: [] }];
            } else
                cases = [
                    {
                        ...(data.source_kind === "poll" ? poll() : data.source_kind === "graph_approval" ? hitl() : proposal()),
                        question: String(data.question),
                        policy: data.policy,
                        participant_uids: data.participant_uids as string[],
                    },
                ];
            return route.fulfill({ json: { ...cases[0], replayed: !!config.ambiguous } });
        }
    );
    return calls;
}
async function loaded(page: Page, suffix = "") {
    await page.goto(fixture + suffix);
    await expect(page.getByRole("heading", { name: "Native Decision card", exact: true })).toBeVisible();
}
async function compose(page: Page) {
    await page.getByRole("button", { name: "Actions", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Add decision", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("textbox", { name: "Question", exact: true })).toBeFocused();
}
async function confirmActors(page: Page, count: number) {
    await page.getByRole("button", { name: "Preview participants", exact: true }).click();
    await page.getByLabel(`I confirm these ${count} resolved participants`, { exact: true }).check();
}
async function expand(page: Page) {
    await page.locator("[data-card-outline-section=decisions] > details > summary").click();
}
for (const width of [320, 390])
    test(`${width}px native optional action, creation and zero-Decision DOM`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const calls = await transport(page);
        await loaded(page);
        await expect.poll(() => calls.filter((call) => call.path.endsWith("/decisions")).length).toBeGreaterThan(0);
        await expect(page.locator("[data-card-outline-section=decisions]")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Add decision", exact: true, includeHidden: true })).toHaveCount(0);
        await compose(page);
        await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect(page.locator("[data-card-outline-section=decisions]")).toHaveCount(0);
        await page.getByRole("button", { name: "Add decision", exact: true }).click();
        await page.getByLabel("Question", { exact: true }).fill("Approve the explicitly selected plan?");
        await page.getByLabel("Ada Reader", { exact: true }).check();
        await page.getByLabel("Grace Reader", { exact: true }).check();
        await confirmActors(page, 2);
        await page.getByRole("combobox", { name: "Approval policy", exact: true }).selectOption("sequence");
        await page.getByRole("button", { name: "Move earlier Grace Reader", exact: true }).click();
        await page.getByLabel("I confirm the question, policy and 2 selected participants", { exact: true }).focus();
        await page.keyboard.press("Space");
        await page.getByRole("button", { name: "Create decision", exact: true }).focus();
        await page.keyboard.press("Enter");
        await expect(page.locator("[data-card-outline-section=decisions]")).toBeVisible();
        expect(calls.find((call) => call.method === "POST" && !call.path.endsWith("/preview"))?.data).toMatchObject({
            source_kind: "proposal",
            source_ref: "Card0000001",
            participant_uids: [actor, other],
            actor_sources: [
                { kind: "user", uid: actor },
                { kind: "user", uid: other },
            ],
            expected_actor_snapshot: "d".repeat(64),
            policy: { op: "sequence", required: 0, children: [{ actor_uid: other }, { actor_uid: actor }] },
        });
        await expand(page);
        await expect(page.getByRole("heading", { name: "Approve the explicitly selected plan?", exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(calls.some((call) => call.path.includes("/candidates") && call.query.get("source_kind") === "proposal")).toBe(true);
    });

test("proposal explicit response, immutable UUID retry, duplicate click lock and effect separation", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()], ambiguous: true });
    await loaded(page);
    await expand(page);
    await page.getByLabel("Approve", { exact: true }).check();
    await page.getByLabel("I confirm this response for the displayed round and target", { exact: true }).check();
    await page.getByRole("button", { name: "Submit response", exact: true }).click();
    await expect(page.getByRole("button", { name: "Retry exact command", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit response", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry exact command", exact: true }).click();
    await expect(page.getByText("State: Passed", { exact: true })).toBeVisible();
    await expect(page.getByText("Execution result:", { exact: false })).toContainText("No execution result");
    const writes = calls.filter((call) => call.method === "POST" && !call.path.endsWith("/preview"));
    expect(writes).toHaveLength(2);
    expect(writes[0].data).toEqual(writes[1].data);
    expect(writes[0].data).toMatchObject({
        round_id: round,
        expected_revision: 0,
        target_revision: "target-revision",
        policy_revision: "policy-revision",
        choice: "approve",
    });
    expect(writes[0].data.request_id).toMatch(/^[a-f0-9-]{36}$/);
    await expect(page.getByRole("button", { name: "Submit response", exact: true })).toHaveCount(0);
});
for (const status of [403, 404, 409])
    test(`${status} reload and reconfirm retains safe creation draft`, async ({ page }) => {
        await transport(page, { status });
        await loaded(page);
        await compose(page);
        await page.getByLabel("Question", { exact: true }).fill("Safe original question");
        await page.getByLabel("Ada Reader", { exact: true }).check();
        await confirmActors(page, 1);
        await page.getByRole("combobox", { name: "Approval policy", exact: true }).selectOption("all");
        await page.getByLabel("I confirm the question, policy and 1 selected participants", { exact: true }).check();
        await page.getByRole("button", { name: "Create decision", exact: true }).click();
        await expect(page.getByRole("status")).toContainText("Reload current data and confirm again");
        await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Reload and reconfirm", exact: true }).click();
        await expect(page.getByLabel("Question", { exact: true })).toHaveValue("Safe original question");
        await expect(page.getByLabel("I confirm the question, policy and 0 selected participants", { exact: true })).not.toBeChecked();
        await expect(page.getByLabel("I confirm these 1 resolved participants", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
    });

test("read-only action absent; server allowed_actions controls responses", async ({ page }) => {
    await transport(page, { initial: [{ ...proposal(), allowed_actions: [], state: "needs_info", blocked_reason: "target_revision_changed" }] });
    await loaded(page, "?readonly");
    await page.getByRole("button", { name: "Actions", exact: true }).click();
    await expect(page.getByRole("button", { name: "Add decision", exact: true, includeHidden: true })).toHaveCount(0);
    await expand(page);
    await expect(page.getByRole("radio")).toHaveCount(0);
    await expect(page.getByText("Reason:", { exact: false })).toContainText("The source changed");
});

test("bounded Decision and candidate paging; card switch rejects old content", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()], pagination: true });
    await loaded(page);
    await expand(page);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect.poll(() => calls.some((call) => call.query.get("before") === uid && call.query.get("limit") === "15")).toBe(true);
    await compose(page);
    await page.getByLabel("Search current card readers by name or email", { exact: true }).fill("gra");
    await expect.poll(() => calls.some((call) => call.path.endsWith("/candidates") && call.query.get("query") === "gra")).toBe(true);
    await page.getByRole("button", { name: "Next participants", exact: true }).click();
    await expect.poll(() => calls.some((call) => call.path.endsWith("/candidates") && call.query.get("page") === "2")).toBe(true);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await expect(page.locator("[data-card-outline-section=decisions]")).toHaveCount(0);
});

test("Poll hidden tally, explicit vote, own response and named visible ballots", async ({ page }) => {
    const calls = await transport(page, { initial: [poll()] });
    await loaded(page);
    await expand(page);
    await expect(page.getByText("Tally and other participants’ ballots are hidden", { exact: false })).toBeVisible();
    await expect(page.getByText("Named ballots", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Approve", { exact: true })).toHaveCount(0);
    await page.getByLabel("Proceed with plan", { exact: true }).check();
    await page.getByLabel("I confirm this response for the displayed round and target", { exact: true }).check();
    await page.getByRole("button", { name: "Submit response", exact: true }).click();
    await expect(page.getByText("Option votes: 1", { exact: true })).toBeVisible();
    await expect(page.getByText("Qualifying participation: 1", { exact: true })).toBeVisible();
    await page.getByText("Named ballots", { exact: true }).click();
    await expect(page.getByText(`${other}: Needs information`, { exact: true })).toBeVisible();
    expect(calls.find((call) => call.method === "POST" && !call.path.endsWith("/preview"))?.data).toMatchObject({
        choice: "vote",
        option_uid: "Opt00000001",
    });
    await expect(page.getByRole("button", { name: "Change response", exact: true })).toBeVisible();
});

test("closed Poll shows server winner and forbids new responses", async ({ page }) => {
    await transport(page, { initial: [poll(true, true)] });
    await loaded(page);
    await expand(page);
    await expect(page.getByText("Winning option: Proceed with plan", { exact: true })).toBeVisible();
    await expect(page.getByRole("radio")).toHaveCount(0);
    await expect(page.getByText("State: Passed", { exact: true })).toBeVisible();
});

test("Poll creation requires explicit source authority, options, rules and future deadline", async ({ page }) => {
    const calls = await transport(page);
    await loaded(page);
    await compose(page);
    await page.getByRole("combobox", { name: "Decision type", exact: true }).selectOption("poll");
    await expect.poll(() => calls.some((call) => call.path.endsWith("/candidates") && call.query.get("source_kind") === "poll")).toBe(true);
    await page.getByLabel("Question", { exact: true }).fill("Choose a release plan");
    await page.getByRole("combobox", { name: "Source type", exact: true }).selectOption("team");
    await page.getByLabel("Review team", { exact: true }).check();
    await confirmActors(page, 2);
    await page.getByLabel("Option 1", { exact: true }).fill("Proceed");
    await page.getByLabel("Option 2", { exact: true }).fill("Defer");
    const rules = [
        ["What counts toward participation", "vote_only"],
        ["Winner rule", "majority_of_votes"],
        ["Tie outcome", "needs_info"],
        ["Outcome below quorum", "expired"],
        ["Outcome without a qualifying winner", "rejected"],
        ["Needs information rule", "block_result"],
        ["Allow response changes", "true"],
        ["Tally and named ballot visibility", "after_close"],
    ];
    for (const [label, value] of rules) await page.getByRole("combobox", { name: label, exact: true }).selectOption(value);
    await page.getByLabel("I confirm the question, policy and 2 selected participants", { exact: true }).check();
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
    await page.getByLabel("Deadline (required, local time)", { exact: true }).fill("2030-10-24T10:00");
    await page.getByLabel("I confirm the question, policy and 2 selected participants", { exact: true }).check();
    await page.getByRole("button", { name: "Create decision", exact: true }).click();
    await expect.poll(() => calls.some((call) => call.method === "POST" && !call.path.endsWith("/preview"))).toBe(true);
    const payload = calls.find((call) => call.method === "POST" && !call.path.endsWith("/preview"))!.data;
    expect(payload).toMatchObject({
        source_kind: "poll",
        participant_uids: [actor, other],
        actor_sources: [{ kind: "team", uid: "Team0000001" }],
        expected_actor_snapshot: "d".repeat(64),
        poll: {
            options: [
                { key: "option-1", label: "Proceed" },
                { key: "option-2", label: "Defer" },
            ],
            participation_quorum: 1,
            winner_policy: "majority_of_votes",
            allow_change: true,
            tally_visibility: "after_close",
        },
    });
    expect(payload).not.toHaveProperty("policy");
});

test("401 preserves native logout cleanup and discards transient intent", async ({ page }) => {
    const calls = await transport(page, { status: 401 });
    await loaded(page);
    await compose(page);
    await page.getByLabel("Question", { exact: true }).fill("Transient question");
    await page.getByLabel("Ada Reader", { exact: true }).check();
    await confirmActors(page, 1);
    await page.getByRole("combobox", { name: "Approval policy", exact: true }).selectOption("all");
    await page.getByLabel("I confirm the question, policy and 1 selected participants", { exact: true }).check();
    await page.getByRole("button", { name: "Create decision", exact: true }).click();
    await expect(page.getByTestId("auth-identity")).toHaveText("signed out");
    await expect(page.getByRole("form", { name: "New decision", exact: true })).toHaveCount(0);
    expect(calls.filter((call) => call.method === "POST" && !call.path.endsWith("/preview"))).toHaveLength(1);
});

test("delayed original-card list cannot overwrite the current card", async ({ page }) => {
    await transport(page);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
        release = resolve;
    });
    let entered = false;
    await page.route("**/board/Board000001/card/Card0000001/decisions?**", async (route) => {
        entered = true;
        await held;
        await route.fulfill({ json: { items: [proposal()], next_cursor: null, target_revision: "old-target" } }).catch(() => {});
    });
    await loaded(page);
    await expect.poll(() => entered).toBe(true);
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    release();
    await page.waitForTimeout(350);
    await expect(page.locator("[data-card-outline-section=decisions]")).toHaveCount(0);
});

for (const width of [320, 390])
    test(`${width}px shared Group/Team preview dedupe, counts and exact create snapshot`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const calls = await transport(page);
        await loaded(page);
        await compose(page);
        await page.getByLabel("Question", { exact: true }).fill("Approve with explicit sources?");
        const type = page.getByRole("combobox", { name: "Source type", exact: true });
        await expect(page.getByLabel("Ada Reader", { exact: true })).toBeVisible();
        await expect(type.locator("option[value=group]")).not.toHaveAttribute("disabled");
        await type.selectOption("group");
        await expect(type).toHaveValue("group");
        await page.getByLabel("Review group", { exact: true }).check();
        await type.selectOption("team");
        await page.getByLabel("Review team", { exact: true }).check();
        await page.getByRole("button", { name: "Preview participants", exact: true }).focus();
        await page.keyboard.press("Enter");
        await expect(page.getByText("Unique eligible participants: 2", { exact: true })).toBeVisible();
        await expect(page.getByText("Unique excluded members: 1 (not currently eligible)", { exact: true })).toBeVisible();
        await expect(page.getByText("2 of 3 eligible; 1 excluded", { exact: false })).toHaveCount(2);
        await expect(page.getByText(`Ada Reader (${actor})`, { exact: false })).toHaveCount(1);
        await expect(page.getByText(`Grace Reader (${other})`, { exact: false })).toHaveCount(1);
        await page.getByLabel("I confirm these 2 resolved participants", { exact: true }).focus();
        await page.keyboard.press("Space");
        await page.getByRole("combobox", { name: "Approval policy", exact: true }).selectOption("k_of_n");
        await page.getByLabel("Required approvals", { exact: true }).fill("2");
        await page.getByLabel("I confirm the question, policy and 2 selected participants", { exact: true }).check();
        await page.getByRole("button", { name: "Create decision", exact: true }).click();
        await expect(page.locator("[data-card-outline-section=decisions]")).toBeVisible();
        const command = calls.find((call) => call.method === "POST" && call.path.endsWith("/decisions"))!;
        expect(command.data).toMatchObject({
            actor_sources: [
                { kind: "group", uid: "Group000001" },
                { kind: "team", uid: "Team0000001" },
            ],
            expected_actor_snapshot: "d".repeat(64),
            participant_uids: [actor, other],
            policy: { op: "k_of_n", required: 2, children: [{ actor_uid: actor }, { actor_uid: other }] },
        });
        expect(calls.filter((call) => call.path.endsWith("/preview"))).toHaveLength(1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

test("Group permission absent prevents directory read; source mutation discards preview", async ({ page }) => {
    const calls = await transport(page, { group: false });
    await loaded(page);
    await compose(page);
    await expect(page.getByRole("combobox", { name: "Source type", exact: true }).locator("option[value=group]")).toHaveAttribute("disabled");
    await expect(page.getByText("Group selection requires current group read permission.", { exact: true })).toBeVisible();
    await page.getByLabel("Ada Reader", { exact: true }).check();
    await confirmActors(page, 1);
    await page.getByLabel("Grace Reader", { exact: true }).check();
    await expect(page.getByLabel("I confirm these 1 resolved participants", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
    expect(calls.some((call) => call.query.get("kind") === "group")).toBe(false);
});

for (const status of [400, 403, 404, 409])
    test(`${status} preview failure exposes no resolved identities or creation authority`, async ({ page }) => {
        const calls = await transport(page, { previewStatus: status });
        await loaded(page);
        await compose(page);
        await page.getByLabel("Question", { exact: true }).fill("Preserve same-session question");
        await page.getByRole("combobox", { name: "Source type", exact: true }).selectOption("team");
        await page.getByLabel("Review team", { exact: true }).check();
        await page.getByRole("button", { name: "Preview participants", exact: true }).click();
        await expect(page.getByRole("status")).toContainText(status === 400 ? "Request failed" : "Reload current data and confirm again");
        await expect(page.getByText(`Ada Reader (${actor})`, { exact: false })).toHaveCount(0);
        await expect(page.getByLabel("Question", { exact: true })).toHaveValue("Preserve same-session question");
        await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
        expect(calls.some((call) => call.method === "POST" && call.path.endsWith("/decisions"))).toBe(false);
    });

test("source creation exact UUID retry retains confirmed snapshot and resolved policy", async ({ page }) => {
    const calls = await transport(page, { ambiguous: true });
    await loaded(page);
    await compose(page);
    await page.getByLabel("Question", { exact: true }).fill("Immutable source command");
    await page.getByRole("combobox", { name: "Source type", exact: true }).selectOption("team");
    await page.getByLabel("Review team", { exact: true }).check();
    await confirmActors(page, 2);
    await page.getByRole("combobox", { name: "Approval policy", exact: true }).selectOption("all");
    await page.getByLabel("I confirm the question, policy and 2 selected participants", { exact: true }).check();
    await page.getByRole("button", { name: "Create decision", exact: true }).click();
    await expect(page.getByRole("button", { name: "Retry exact command", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Preview participants", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry exact command", exact: true }).click();
    await expect(page.locator("[data-card-outline-section=decisions]")).toBeVisible();
    const writes = calls.filter((call) => call.method === "POST" && call.path.endsWith("/decisions"));
    expect(writes).toHaveLength(2);
    expect(writes[0].data).toEqual(writes[1].data);
    expect(calls.filter((call) => call.path.endsWith("/preview"))).toHaveLength(1);
});

test("delayed preview cannot restore actors after source mutation or native card switch", async ({ page }) => {
    const calls = await transport(page, { previewDelay: 700 });
    await loaded(page);
    await compose(page);
    await page.getByLabel("Ada Reader", { exact: true }).check();
    await page.getByRole("button", { name: "Preview participants", exact: true }).click();
    await expect.poll(() => calls.filter((call) => call.path.endsWith("/preview")).length).toBe(1);
    await page.getByRole("button", { name: "Remove source Ada Reader", exact: true }).click();
    await page.getByLabel("Grace Reader", { exact: true }).check();
    await confirmActors(page, 1);
    await expect(page.getByText(`Ada Reader (${actor})`, { exact: false })).toHaveCount(0);
    await expect(page.getByText(`Grace Reader (${other})`, { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Preview participants", exact: true }).click();
    await expect.poll(() => calls.filter((call) => call.path.endsWith("/preview")).length).toBe(3);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await compose(page);
    await expect(page.getByLabel("Ada Reader", { exact: true })).toBeVisible();
    await expect(page.getByText(`Grace Reader (${other})`, { exact: false })).toHaveCount(0);
    await expect(page.getByLabel("I confirm these 1 resolved participants", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
});

const gateBinding = () => ({
    target_stage: "review",
    decision_uid: uid,
    round_id: round,
    target_revision: "target-revision",
    policy_revision: "policy-revision",
});
async function chooseGate(page: Page) {
    const gate = page.getByRole("region", { name: "Stage gates", exact: true });
    await gate.getByRole("combobox", { name: "Target stage", exact: true }).selectOption("review");
    await gate.getByRole("combobox", { name: "Gate proposal", exact: true }).selectOption(uid);
    await gate.getByLabel("I confirm this gate change and the displayed proposal identity", { exact: true }).check();
    return gate;
}
for (const operation of ["read", "write"] as const)
    for (const status of [403, 409])
        test(`gate ${operation} ${status} and recovery status follow locale changes without reload`, async ({ page }, testInfo) => {
            const calls = await transport(page, {
                initial: [proposal()],
                ...(operation === "read" ? { gateReadStatus: status } : { gateStatus: status }),
            });
            await loaded(page);
            await expand(page);
            const gate = page.locator("[data-card-outline-section=decisions] section").filter({ has: page.locator("form") });
            if (operation === "write") {
                await chooseGate(page);
                await gate.getByRole("button", { name: "Save stage gate", exact: true }).click();
            }
            const recovery = page.locator("[data-card-outline-section=decisions]").getByRole("status");
            await expect(recovery).toContainText("Reload current data and confirm again");
            const reads = calls.filter((call) => call.method === "GET").length;
            for (const locale of locales) {
                const { decision: labels } = JSON.parse(
                    readFileSync(new URL(`../../../../../assets/locales/${locale}/card.json`, import.meta.url), "utf8")
                );
                await page.evaluate(async (locale) => {
                    const modulePath = "/src/i18n.ts";
                    await (await import(/* @vite-ignore */ modulePath)).default.changeLanguage(locale);
                }, locale);
                await expect.soft(gate.locator("p[aria-live=polite]")).toHaveText(labels[`Gate ${operation} failed`], { timeout: 1500 });
                await expect.soft(recovery).toHaveText(labels["Reload required"], { timeout: 1500 });
                await expect(gate.getByRole("button", { name: labels["Save stage gate"], exact: true })).toBeDisabled();
                await expect(page.getByRole("button", { name: labels.Reload, exact: true })).toBeEnabled();
                await expect(page.locator("body")).not.toContainText(privateDecisionDetail);
            }
            expect(calls.filter((call) => call.method === "GET")).toHaveLength(reads);
            expect(calls.filter((call) => call.method === "PUT")).toHaveLength(operation === "write" ? 1 : 0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await page.screenshot({ path: testInfo.outputPath("decision-locale-status.png"), fullPage: true });
        });

test("stage gate saves exact proposal identity, filters inactive stages and removes with confirmation", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()] });
    await loaded(page);
    await expand(page);
    const gate = await chooseGate(page);
    await expect(gate.locator("option[value=retired]")).toHaveCount(0);
    await gate.getByRole("button", { name: "Save stage gate", exact: true }).click();
    await expect(gate.getByRole("button", { name: "Remove gate for review", exact: true })).toBeVisible();
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.data)).toEqual([gateBinding()]);
    await gate.getByRole("button", { name: "Remove gate for review", exact: true }).click();
    await expect(gate.getByRole("button", { name: "Confirm gate removal", exact: true })).toBeDisabled();
    await gate.getByLabel("I confirm this gate change and the displayed proposal identity", { exact: true }).check();
    await gate.getByRole("button", { name: "Confirm gate removal", exact: true }).click();
    await expect(gate.getByText("No stage gates configured.", { exact: true })).toBeVisible();
    expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual([
        "/board/Board000001/card/Card0000001/decision-gate/review",
    ]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test("read-only gate retains readable proposal and revisions without configuration controls", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()], gateInitial: [gateBinding()] });
    await loaded(page, "?readonly");
    await expand(page);
    const gate = page.getByRole("region", { name: "Stage gates", exact: true });
    await expect(gate.getByText("Gate proposal: Approve the original plan? (Dec00000001)", { exact: true })).toBeVisible();
    await expect(gate.getByText(`Round: ${round}`, { exact: true })).toBeVisible();
    await expect(gate.getByRole("button")).toHaveCount(0);
    await expect(gate.getByRole("combobox")).toHaveCount(0);
    expect(calls.some((call) => ["PUT", "DELETE"].includes(call.method))).toBe(false);
});
test("Poll is never a gate proposal and zero decisions add no gate section", async ({ page }) => {
    await transport(page, { initial: [poll()] });
    await loaded(page);
    await expand(page);
    const gate = page.getByRole("region", { name: "Stage gates", exact: true });
    await expect(gate.getByRole("combobox", { name: "Gate proposal", exact: true }).locator("option")).toHaveCount(1);
    await expect(gate.getByRole("button", { name: "Save stage gate", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await expect(gate).toHaveCount(0);
});
test("ambiguous gate mutation requires read before retry and preserves committed binding", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()], gateAmbiguous: true });
    await loaded(page);
    await expand(page);
    const gate = await chooseGate(page);
    await gate.getByRole("button", { name: "Save stage gate", exact: true }).click();
    await expect(gate.getByRole("button", { name: "Save stage gate", exact: true })).toBeDisabled();
    const reads = calls.filter((call) => call.method === "GET" && call.path.endsWith("decision-gate")).length;
    await gate.getByRole("button", { name: "Reload stage gates", exact: true }).click();
    await expect(gate.getByRole("button", { name: "Remove gate for review", exact: true })).toBeVisible();
    expect(calls.filter((call) => call.method === "GET" && call.path.endsWith("decision-gate")).length).toBeGreaterThan(reads);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
});
for (const status of [403, 409])
    test(`${status} gate write requires current data and reconfirmation`, async ({ page }) => {
        const calls = await transport(page, { initial: [proposal()], gateStatus: status });
        await loaded(page);
        await expand(page);
        const gate = await chooseGate(page);
        await gate.getByRole("button", { name: "Save stage gate", exact: true }).click();
        await expect(gate.getByRole("button", { name: "Save stage gate", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Reload and reconfirm", exact: true }).click();
        await expect(gate.getByLabel("I confirm this gate change and the displayed proposal identity", { exact: true })).not.toBeChecked();
        expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    });
test("delayed gate read cannot apply after a card switch", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()], gateInitial: [gateBinding()], gateDelay: 700 });
    await loaded(page);
    await expand(page);
    await expect.poll(() => calls.filter((call) => call.path.endsWith("decision-gate")).length).toBeGreaterThan(0);
    await page.getByRole("button", { name: "Switch card", exact: true }).click();
    await expect(page.getByRole("region", { name: "Stage gates", exact: true })).toHaveCount(0);
    await expect(page.getByText("Remove gate for review", { exact: true })).toHaveCount(0);
});

test("gate cannot write a confirmation retained from an expired session", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()] });
    await loaded(page);
    await expand(page);
    const gate = await chooseGate(page);
    await page.getByRole("button", { name: "Expire session", exact: true }).click();
    await expect(page.getByTestId("auth-identity")).toHaveText("signed out");
    await expect(gate).toHaveCount(0);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
});
test("late gate read from expired session cannot expose retained bindings", async ({ page }) => {
    const calls = await transport(page, { initial: [proposal()], gateInitial: [gateBinding()], gateDelay: 700 });
    await loaded(page);
    await expand(page);
    await expect.poll(() => calls.filter((call) => call.path.endsWith("decision-gate")).length).toBeGreaterThan(0);
    await page.getByRole("button", { name: "Expire session", exact: true }).click();
    await expect(page.getByTestId("auth-identity")).toHaveText("signed out");
    await expect(page.getByRole("region", { name: "Stage gates", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove gate for review", exact: true })).toHaveCount(0);
});

async function composeHITL(page: Page) {
    await compose(page);
    await page.getByRole("combobox", { name: "Decision type", exact: true }).selectOption("graph_approval");
    await page.getByRole("combobox", { name: "Native approval", exact: true }).selectOption("Approval001");
    await page.getByLabel("Question", { exact: true }).fill("Approve native execution?");
    await page.getByLabel("Ada Reader", { exact: true }).check();
    await confirmActors(page, 1);
    await page.getByRole("combobox", { name: "Approval policy", exact: true }).selectOption("all");
    await page.getByLabel("I confirm the question, policy and 1 selected participants", { exact: true }).check();
}
test("HITL creates exact same-card pending binding using proposal participant authorization", async ({ page }) => {
    const calls = await transport(page);
    await loaded(page);
    await composeHITL(page);
    const picker = page.getByRole("combobox", { name: "Native approval", exact: true });
    await expect(picker.locator("option")).toHaveCount(2);
    expect(calls.find((call) => call.path.endsWith("/graph-approvals"))?.query.get("scope_uid")).toBe("Card0000001");
    expect(calls.find((call) => call.path.endsWith("/graph-approvals"))?.query.get("scope_table")).toBe("card");
    expect(calls.find((call) => call.path.endsWith("/graph-approvals"))?.query.get("status")).toBe("pending");
    await page.getByRole("button", { name: "Create decision", exact: true }).click();
    await expand(page);
    await expect(page.getByText("HITL policy state: Pending", { exact: true })).toBeVisible();
    await page.getByText("Original source and policy", { exact: true }).click();
    await expect(page.getByText("Approval001", { exact: true })).toBeVisible();
    const write = calls.find((call) => call.method === "POST" && call.path.endsWith("/decisions"));
    expect(write?.data).toMatchObject({
        source_kind: "graph_approval",
        source_ref: "Card0000001",
        approval_uid: "Approval001",
        policy: { op: "all" },
        participant_uids: [actor],
    });
    expect(write?.data).not.toHaveProperty("poll");
    expect(calls.filter((call) => call.path.endsWith("/preview")).every((call) => call.data.source_kind === "proposal")).toBe(true);
    expect(calls.filter((call) => call.path.endsWith("/candidates")).every((call) => call.query.get("source_kind") === "proposal")).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test("HITL response persists policy pass without execution or native resume", async ({ page }) => {
    const calls = await transport(page, { initial: [hitl()], ambiguous: true });
    const nativeCalls: string[] = [];
    page.on("request", (request) => {
        if (/resume|claim|complete/.test(new URL(request.url()).pathname)) nativeCalls.push(request.url());
    });
    await loaded(page);
    await expand(page);
    await page.getByLabel("Approve", { exact: true }).check();
    await page.getByLabel("I confirm this response for the displayed round and target", { exact: true }).check();
    await page.getByRole("button", { name: "Submit response", exact: true }).click();
    await expect(page.getByRole("button", { name: "Submit response", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry exact command", exact: true }).click();
    await expect(page.getByText("HITL policy state: Passed", { exact: true })).toBeVisible();
    await expect(page.getByText("Policy passed. Execution has not started from this response.", { exact: true })).toBeVisible();
    await expect(page.getByText("Automatic resume is off.", { exact: false })).toBeVisible();
    await expect(page.getByText("Execution result:", { exact: false })).toContainText("No execution result");
    const writes = calls.filter((call) => call.method === "POST");
    expect(writes).toHaveLength(2);
    expect(writes[0].data).toEqual(writes[1].data);
    expect(writes[0].data).toMatchObject({
        round_id: round,
        expected_revision: 0,
        target_revision: "target-revision",
        policy_revision: "policy-revision",
        choice: "approve",
    });
    expect(nativeCalls).toEqual([]);
    const gate = page.getByRole("region", { name: "Stage gates", exact: true });
    await expect(gate.getByRole("combobox", { name: "Gate proposal", exact: true }).locator("option")).toHaveCount(1);
});
test("HITL no pending approval cannot create; Poll stays separate", async ({ page }) => {
    const calls = await transport(page, { noApprovals: true });
    await loaded(page);
    await compose(page);
    await page.getByRole("combobox", { name: "Decision type", exact: true }).selectOption("graph_approval");
    await expect(page.getByText("No pending approvals for this card.", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
    await page.getByRole("combobox", { name: "Decision type", exact: true }).selectOption("poll");
    await expect(page.getByRole("combobox", { name: "Native approval", exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Approval policy", exact: true })).toHaveCount(0);
    expect(calls.filter((call) => call.method === "POST" && call.path.endsWith("/decisions"))).toHaveLength(0);
});
test("HITL readonly card displays policy without creation or response authority", async ({ page }) => {
    const calls = await transport(page, { initial: [{ ...hitl(), allowed_actions: [] }] });
    await loaded(page, "?readonly");
    await page.getByRole("button", { name: "Actions", exact: true }).click();
    await expect(page.getByRole("button", { name: "Add decision", exact: true, includeHidden: true })).toHaveCount(0);
    await expand(page);
    await expect(page.getByText("HITL policy state: Pending", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit response", exact: true })).toHaveCount(0);
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
});
test("HITL confirmed creation loses authority on expired session", async ({ page }) => {
    const calls = await transport(page);
    await loaded(page);
    await composeHITL(page);
    await page.getByRole("button", { name: "Expire session", exact: true, includeHidden: true }).dispatchEvent("click");
    await expect(page.getByTestId("auth-identity")).toHaveText("signed out");
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toHaveCount(0);
    expect(calls.filter((call) => call.method === "POST" && call.path.endsWith("/decisions"))).toHaveLength(0);
});
test("HITL ambiguous creation reuses immutable exact approval binding", async ({ page }) => {
    const calls = await transport(page, { ambiguous: true });
    await loaded(page);
    await composeHITL(page);
    await page.getByRole("button", { name: "Create decision", exact: true }).click();
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry exact command", exact: true }).click();
    await expect(page.locator("[data-card-outline-section=decisions]")).toBeVisible();
    const writes = calls.filter((call) => call.method === "POST" && call.path.endsWith("/decisions"));
    expect(writes).toHaveLength(2);
    expect(writes[0].data).toEqual(writes[1].data);
    expect(writes[0].data.approval_uid).toBe("Approval001");
});

test("HITL delayed approval list cannot cross card or expired session", async ({ page }) => {
    const calls = await transport(page, { approvalDelay: 700 });
    await loaded(page);
    await compose(page);
    await page.getByRole("combobox", { name: "Decision type", exact: true }).selectOption("graph_approval");
    await expect.poll(() => calls.some((call) => call.path.endsWith("/graph-approvals"))).toBe(true);
    await page.getByRole("button", { name: "Switch card", exact: true, includeHidden: true }).dispatchEvent("click");
    await expect(page.getByRole("button", { name: "Create decision", exact: true })).toHaveCount(0);
    await compose(page);
    await page.getByRole("combobox", { name: "Decision type", exact: true }).selectOption("graph_approval");
    await expect(page.getByRole("combobox", { name: "Native approval", exact: true }).locator("option[value=Approval001]")).toHaveCount(0);
    await page.getByRole("button", { name: "Expire session", exact: true, includeHidden: true }).dispatchEvent("click");
    await expect(page.getByTestId("auth-identity")).toHaveText("signed out");
    await expect(page.getByRole("combobox", { name: "Native approval", exact: true })).toHaveCount(0);
    expect(calls.filter((call) => call.method === "POST" && call.path.endsWith("/decisions"))).toHaveLength(0);
});
for (const status of [403, 409])
    test(`HITL ${status} native deny requires reread and fresh confirmation`, async ({ page }) => {
        const calls = await transport(page, { status });
        await loaded(page);
        await composeHITL(page);
        await page.getByRole("button", { name: "Create decision", exact: true }).click();
        await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Reload and reconfirm", exact: true }).click();
        await expect(page.getByLabel("I confirm the question, policy and 0 selected participants", { exact: true })).not.toBeChecked();
        await expect(page.getByRole("button", { name: "Create decision", exact: true })).toBeDisabled();
        expect(calls.filter((call) => call.method === "POST" && call.path.endsWith("/decisions"))).toHaveLength(1);
    });

for (const failure of ["network", 503] as const)
    test(`failed authoritative ${failure} reread locks retained decisions until successful explicit reload`, async ({ page }) => {
        const calls = await transport(page, { initial: [proposal()], readFailure: failure });
        await loaded(page);
        await expand(page);
        await page.getByLabel("Approve", { exact: true }).check();
        await page.getByLabel("I confirm this response for the displayed round and target", { exact: true }).check();
        await expect(page.getByRole("button", { name: "Submit response", exact: true })).toBeEnabled();
        await page.getByRole("button", { name: "Refresh current data", exact: true }).click();
        const reload = page.getByRole("button", { name: "Reload and reconfirm", exact: true });
        await expect(reload).toBeEnabled();
        await expect(page.getByRole("button", { name: "Submit response", exact: true })).toBeDisabled();
        await expect(page.getByRole("button", { name: "Retry exact command", exact: true })).toHaveCount(0);
        await reload.click();
        await expect.poll(() => calls.filter((call) => call.method === "GET" && call.path.endsWith("/decisions")).length).toBe(3);
        await expect(reload).toBeEnabled();
        await expect(page.getByRole("button", { name: "Submit response", exact: true })).toBeDisabled();
        await reload.click();
        await expect(reload).toHaveCount(0);
        await expect(page.getByLabel("I confirm this response for the displayed round and target", { exact: true })).not.toBeChecked();
        await page.getByLabel("Approve", { exact: true }).check();
        await page.getByLabel("I confirm this response for the displayed round and target", { exact: true }).check();
        await expect(page.getByRole("button", { name: "Submit response", exact: true })).toBeEnabled();
        expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
    });
