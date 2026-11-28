import assert from "node:assert/strict";
import { test } from "node:test";
import { regressions, scanSource } from "./verify-ui-strings.mjs";

test("raw accessible text, dialog and validation literals are detected; translated and user content are excluded", () => {
    const found = scanSource(
        "Example.tsx",
        `
        const view = <><button aria-label="Open menu">Open</button><input placeholder={"Name"}/>
            <span>{user.title}</span><span>{t("common.Close")}</span><BoardCardSection title="card.Description"/></>;
        window.confirm("Delete this item?"); Toast.Add.error("Save failed"); z.string().min(1, { message: "Required" });
    `
    );
    assert.deepEqual(
        found.map((x) => [x.kind, x.text]),
        [
            ["attribute:aria-label", "Open menu"],
            ["jsx-text", "Open"],
            ["attribute:placeholder", "Name"],
            ["dialog-toast", "Delete this item?"],
            ["dialog-toast", "Save failed"],
            ["validation", "Required"],
        ]
    );
    assert.equal(regressions(found, []).length, 6);
    assert.equal(regressions(found, found).length, 0);
    assert.equal(regressions([...found, found[0]], found).length, 1);
    assert.equal(regressions(found, found.slice(1)).length, 1);
    const translated = `<button aria-label={t("common.Open")}>
        {user.title}</button>`;
    assert.equal(regressions(scanSource("Example.tsx", translated), found).length, 0);
});

test("TypeScript generics and HTML spacing entities are not user-facing messages", () => {
    assert.deepEqual(scanSource("Models.ts", "const identity = <T>(value: T): T => value;"), []);
    assert.deepEqual(scanSource("Spacing.tsx", "const view = <span>&nbsp;</span>;"), []);
});

test("missing fixed translation keys fail even when every locale omits them", () => {
    const resources = { common: { Close: "Close", count_one: "Item", count_other: "Items", "Done.": "Done." }, cron: { months: ["January"] } };
    const source = `
        t("common.Close");
        t((ready ? "common.Confirm" : "common.Done."));
        t("common.count", { count });
        t("cron.months", { returnObjects: true });
        t(path);
    `;
    assert.deepEqual(scanSource("Example.tsx", source, resources), [
        { file: "Example.tsx", line: 3, kind: "missing-translation-key", text: "common.Confirm" },
    ]);
});

test("i18n instance calls validate fixed keys while preserving dynamic and user content", () => {
    const resources = { settings: { "Model pull could not be completed": "Model pull could not be completed" } };
    const source = `i18n.t("settings.Model pull could not be completed");
        i18n.t("settings.Missing model message");
        i18n.t(user.message); i18n.t(key);`;
    assert.deepEqual(scanSource("Messages.ts", source, resources), [
        { file: "Messages.ts", line: 2, kind: "missing-translation-key", text: "settings.Missing model message" },
    ]);
});

test("persisted creation defaults are detected without treating user input as literals", () => {
    const source = `createWikiMutateAsync({title: "New page"}); createCheckitemMutateAsync({title: t("card.New checkitem")});
        createProjectLabelMutateAsync({name: form.name, description: "Sample description"});`;
    assert.deepEqual(
        scanSource("Create.tsx", source).map(({ kind, text }) => [kind, text]),
        [
            ["creation-default", "New page"],
            ["creation-default", "Sample description"],
        ]
    );
});

test("activity rail labels and fixed accessible template text are checked without rewriting model content", () => {
    const found = scanSource(
        "Example.tsx",
        `
        const view = <Layout activityRailItems={[{ label: "Explorer" }, { label: t("common.Explorer") }]} />;
        const images = <><img alt={\`\${drawingType} diagram\`}/><img alt={user.title}/><img alt={t("editor.Code drawing")}/></>;
        const language = { label: "JavaScript" };
    `
    );
    assert.deepEqual(
        found.map(({ kind, text }) => [kind, text]),
        [
            ["activity-rail-label", "Explorer"],
            ["attribute-template:alt", "diagram"],
        ]
    );
});

test("rendered branches and concatenations cannot hide untranslated text", () => {
    const source = `
        const view = <>
            <button aria-label={open ? "Close menu" : "Open menu"}>{open ? "Close" : t("common.Open")}</button>
            <span>{loading && "Loading"}</span><span>{name ?? "Unnamed"}</span>
            <span>{"Page " + page}</span><span>{\`Items: \${count}\`}</span>
            <span>{kind === "internal" ? user.title : t("common.External")}</span>
        </>;
    `;
    assert.deepEqual(
        scanSource("Example.tsx", source).map(({ text }) => text),
        ["Close menu", "Open menu", "Close", "Loading", "Unnamed", "Page", "Items:"]
    );
});

test("custom tooltip/label props and computed toast messages retain untranslated literals", () => {
    const found = scanSource(
        "Messages.tsx",
        `
        const controls = <><ToolbarButton tooltip="Task list"/><TaskLink label={ready ? "Review" : t("common.Waiting")}/></>;
        Toast.Add.error(failed ? "Save failed" : t("common.Done"));
        window.confirm(\`Delete \${name}?\`);
        z.string().refine(valid, { message: missing ? "Required" : t("common.Invalid") });
        Toast.Add.error(error.message); Toast.Add.success(t("common.Saved"));
        const userContent = <><TaskLink label={user.title}/><span>{user.description}</span><TaskLink label={\`\${user.firstname} \${user.lastname}\`}/></>;
    `
    );
    assert.deepEqual(
        found.map(({ kind, text }) => [kind, text]),
        [
            ["attribute:tooltip", "Task list"],
            ["attribute:label", "Review"],
            ["dialog-toast", "Save failed"],
            ["dialog-toast", "Delete ?"],
            ["validation", "Required"],
        ]
    );
});

test("Video.js storyboard metadata identity is excluded without hiding caption or accessible labels", () => {
    const source = `<><track kind="metadata" label="thumbnails" src={storyboard} default />
        <track kind="captions" label="thumbnails" />
        <track kind="subtitles" label="English" />
        <track label="thumbnails" />
        <track kind="metadata" label="Other label" />
        <button aria-label="thumbnails" />
        <Track kind="metadata" label="thumbnails" /></>`;
    assert.deepEqual(
        scanSource("Storyboard.tsx", source).map(({ kind, text }) => [kind, text]),
        [
            ["attribute:label", "thumbnails"],
            ["attribute:label", "English"],
            ["attribute:label", "thumbnails"],
            ["attribute:label", "Other label"],
            ["attribute:aria-label", "thumbnails"],
            ["attribute:label", "thumbnails"],
        ]
    );
});

test("native block placeholder options cannot hide untranslated text", () => {
    const source = `BlockPlaceholderPlugin.configure({ options: { placeholders: { [KEYS.p]: "Type something...", h1: t("editor.Heading"), h2: user.title } } });
        const metadata = { placeholders: { title: "Not a rendered plugin option" } };`;
    assert.deepEqual(
        scanSource("Placeholder.tsx", source).map(({ kind, text }) => [kind, text]),
        [["block-placeholder", "Type something..."]]
    );
});
