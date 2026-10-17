const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const filename = resolve(__dirname, "../src/pages/BoardPage/components/card/checklist/BoardCardCheckitem.tsx");
const code = ts.transpileModule(readFileSync(filename, "utf8") + "\nexport { BoardCardCheckitemDisplay };", {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

// Execute the production display component with controlled model/context hooks.
// Child widgets remain inert: this checks its render/permission boundary, not DOM.
for (const [name, assignedUser, canUpdate, expected] of [
    ["unassigned undefined, read only", undefined, false, false],
    ["unassigned null, read only", null, false, false],
    ["unassigned, card editor", undefined, true, true],
    ["assigned to self, no card edit role", { uid: "viewer" }, false, true],
    ["assigned to another user, card editor", { uid: "other" }, true, false],
]) {
    test(name, () => {
        const exports = {};
        const jsx = (type, props) => ({ type, props });
        runInNewContext(
            code,
            {
                exports,
                require(name) {
                    if (name === "react") return { memo: (fn) => fn, useState: (value) => [value, () => {}] };
                    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
                    if (name === "react-i18next") return { useTranslation: () => [(key) => key] };
                    if (name === "@/core/providers/BoardCardProvider")
                        return {
                            useBoardCard: () => ({
                                projectUID: "project",
                                card: { uid: "card" },
                                currentUser: { uid: "viewer" },
                                hasRoleAction: () => canUpdate,
                                sharedClassNames: {},
                            }),
                        };
                    if (name === "@/core/hooks/usePageNavigate") return { usePageNavigateRef: () => () => {} };
                    if (name === "@/core/models/roles") return { ProjectRole: { EAction: { CardUpdate: "card_update" } } };
                    if (name === "@/core/models/ModelRegistry") return { ModelRegistry: { ProjectCheckitem: { Provider: "checkitem-provider" } } };
                    if (name === "@/core/utils/ComponentUtils") return { cn: (...parts) => parts.filter(Boolean).join(" ") };
                    if (name === "@/components/base/Tooltip")
                        return { __esModule: true, default: { Root: "tooltip", Trigger: "trigger", Content: "content" } };
                    return { __esModule: true, default: name };
                },
            },
            { filename }
        );
        const view = exports.BoardCardCheckitemDisplay({
            checkitem: {
                uid: "item",
                useField: (name) => ({ title: "Read-only task", is_checked: false })[name],
                useForeignFieldOne: (name) => (name === "user" ? assignedUser : undefined),
            },
            canReorder: canUpdate,
            draggableRef: { current: null },
        });
        assert.equal(view.type, "checkitem-provider");
        assert.equal(view.props.params.canEdit, expected);
    });
}
