import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import SourceCardLink, { sourceCardPath } from "./SourceCardLink";

const source = { type: "project_card", uid: "Card123", project_uid: "Board456", url: "/board/Board456/Card123" };

test("provenance renders only a generic label and exact native source route", () => {
    const html = renderToStaticMarkup(
        <SourceCardLink
            source={{ ...source, title: "PRIVATE TITLE", body: "PRIVATE BODY", author: "PRIVATE AUTHOR" }}
            label="Source card"
            navigate={() => {}}
        />
    );
    assert.match(html, /href="\/board\/Board456\/Card123"/);
    assert.match(html, />Source card<\/a>/);
    assert.doesNotMatch(html, /PRIVATE/);
});

test("malformed, external, mismatched, and encoded source paths remain plain text", () => {
    for (const invalid of [
        undefined,
        null,
        "javascript:alert(1)",
        {},
        { ...source, type: "external" },
        ...[
            "javascript:alert(1)",
            "//evil.test/board/Board456/Card123",
            "https://evil.test/board/Board456/Card123",
            "/board/Other/Card123",
            "/board/Board456/Card123?private=1",
            "/board/Board456/Card123#private",
            "/board/Board456/%2e%2e",
        ].map((url) => ({ ...source, url })),
        { ...source, uid: "..", url: "/board/Board456/.." },
        { ...source, project_uid: "evil\\host" },
    ]) {
        assert.equal(sourceCardPath(invalid), undefined);
        assert.equal(renderToStaticMarkup(<SourceCardLink source={invalid} label="Source card" navigate={() => {}} />), "<span>Source card</span>");
    }
});

test("source-only linked history and full cloned history share safe provenance", () => {
    for (const history of [{ source }, { source, project: { title: "Destination" }, card: { title: "Clone" }, clone_identity: "opaque" }]) {
        assert.equal(sourceCardPath(history.source), "/board/Board456/Card123");
    }
});

test("all native activity locales render linked and cloned events in both timeline views", async () => {
    const { createInstance } = await import("i18next");
    const { Trans } = await import("react-i18next");
    const { readFileSync } = await import("node:fs");
    let expectedKeys: string[] | undefined;
    for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
        const activity = JSON.parse(readFileSync(`src/assets/locales/${locale}/activity.json`, "utf8"));
        const keys = Object.keys(activity.project).map(String).sort();
        if (expectedKeys) assert.deepEqual(keys, expectedKeys);
        expectedKeys = keys;
        const i18n = createInstance();
        await i18n.init({ lng: locale, resources: { [locale]: { translation: { activity } } } });
        for (const event of ["card_linked", "card_cloned"]) {
            for (const view of ["default", "user"]) {
                const html = renderToStaticMarkup(
                    <Trans
                        i18n={i18n}
                        i18nKey={`activity.project.${event}.${view}`}
                        components={{
                            Recorder: <span>Recorder</span>,
                            SourceCard: <SourceCardLink source={source} label={activity["Source card"]} navigate={() => {}} />,
                        }}
                    />
                );
                assert.match(html, /href="\/board\/Board456\/Card123"/);
                assert.ok(html.includes(activity["Source card"]));
                assert.doesNotMatch(html, /activity\.project|SourceCard|<Project|<Card/);
            }
        }
    }
});
