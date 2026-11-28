const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const ts = require(path.join(root, "node_modules/typescript"));
const i18next = require(path.join(root, "node_modules/i18next"));
const component = (name) => Object.assign((props) => ({ type: name, props }), { displayName: name });
const base = (name) =>
    new Proxy(component(name), {
        get: (target, key) => (key === "__esModule" ? true : key === "default" ? target : component(`${name}.${String(key)}`)),
    });
const jsx = (type, props) => ({ type, props: props || {} });
const hooks = {
    useState: (value) => [value, () => {}],
    useRef: () => ({ current: null }),
    useEffect: () => {},
    useCallback: (f) => f,
    useMemo: (f) => f(),
};
const KEYS = { audio: "audio", file: "file", img: "img", video: "video" };
let i18n;
function load(file, append = "") {
    const text = fs.readFileSync(path.join(root, "src", file), "utf8") + append;
    const compiled = ts.transpileModule(text, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    const module = { exports: {} };
    const mockRequire = (name) => {
        if (name === "react") return { __esModule: true, ...hooks };
        if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "Fragment" };
        if (name === "react-i18next") return { useTranslation: () => [i18n.t.bind(i18n)] };
        if (name === "platejs") return { KEYS, isUrl: () => true };
        if (name === "platejs/react") return { useEditorRef: () => ({}), useReadOnly: () => false, useElement: () => ({ lang: "plaintext" }) };
        if (name === "use-file-picker") return { useFilePicker: () => ({ openFilePicker() {} }) };
        if (name === "@platejs/code-drawing")
            return {
                VIEW_MODE: { Both: "Both", Code: "Code", Image: "Image" },
                VIEW_MODE_ARRAY: ["Both", "Code", "Image"].map((value) => ({ value, label: value })),
                CODE_DRAWING_TYPE_ARRAY: [{ value: "Mermaid", label: "Mermaid" }],
            };
        if (name.includes("ComponentUtils")) return { cn: (...xs) => xs.filter(Boolean).join(" ") };
        return base(name);
    };
    vm.runInNewContext(compiled, { require: mockRequire, module, exports: module.exports, setTimeout: () => 0, navigator: {} }, { filename: file });
    return module.exports;
}
function nodes(tree) {
    if (!tree || typeof tree !== "object") return [];
    if (Array.isArray(tree)) return tree.flatMap(nodes);
    return [tree, ...nodes(tree.props?.children)];
}
function strings(tree) {
    return nodes(tree)
        .flatMap((n) => [n.props?.children])
        .flat(Infinity)
        .filter((n) => typeof n === "string");
}
(async () => {
    let assertions = 0;
    for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
        i18n = i18next.createInstance();
        await i18n.init({
            lng: locale,
            fallbackLng: false,
            resources: {
                [locale]: {
                    translation: {
                        editor: JSON.parse(fs.readFileSync(path.join(root, `src/assets/locales/${locale}/editor.json`))),
                        common: JSON.parse(fs.readFileSync(path.join(root, `src/assets/locales/${locale}/common.json`))),
                    },
                },
            },
        });
        const media = load("components/plate-ui/media-toolbar-button.tsx", "\nexport { MediaUrlDialogContent, MEDIA_CONFIG };");
        for (const nodeType of Object.values(KEYS)) {
            const config = media.MEDIA_CONFIG[nodeType];
            const tree = media.MediaUrlDialogContent({ currentConfig: config, nodeType, isOpened: true, setOpen() {} });
            assert(strings(tree).includes(i18n.t(config.title)));
            assertions++;
            assert(!strings(tree).includes(config.title));
            assertions++;
            assert(nodes(tree).some((n) => n.props?.placeholder === i18n.t("editor.Paste link")));
            assertions++;
            const toolbar = media.MediaToolbarButton({ nodeType });
            assert(nodes(toolbar).some((n) => n.props?.["aria-label"] === i18n.t(config.title)));
            assertions++;
            assert(nodes(toolbar).some((n) => n.props?.["aria-label"] === i18n.t(config.tooltip)));
            assertions++;
        }
        const drawing = load(
            "components/plate-ui/code-drawing-node.tsx",
            "\nexport { CodeDrawingPreviewArea, CodeDrawingToolbar, CodeDrawingTextarea };"
        );
        for (const isMobile of [false, true]) {
            const toolbar = drawing.CodeDrawingToolbar({
                drawingType: "Mermaid",
                viewMode: "Both",
                isMobile,
                onDrawingTypeChange() {},
                onDrawingModeChange() {},
            });
            for (const key of ["Code drawing", "Diagram engine", "Diagram view"]) {
                assert(nodes(toolbar).some((n) => n.props?.["aria-label"] === i18n.t(`editor.${key}`)));
                assertions++;
            }
        }
        for (const state of [
            { loading: true, error: false, code: "x", image: "", key: "Loading..." },
            { loading: false, error: true, code: "x", image: "", key: "Rendering failed" },
            { loading: false, error: false, code: "", image: "", key: "Preview will appear here" },
        ]) {
            const tree = drawing.CodeDrawingPreviewArea({ ...state, viewMode: "Both" });
            assert(nodes(tree).some((n) => n.props?.role === "status" && n.props.children === i18n.t(`editor.${state.key}`)));
            assertions++;
        }
        const copy = load("components/Markdown/CodeBlock.tsx").default({ code: "original UGC", language: "js" });
        assert(nodes(copy).some((n) => n.props?.["aria-label"] === i18n.t("common.Copy")));
        assertions++;
        assert(nodes(copy).some((n) => n.props?.children === "original UGC"));
        assertions++;
        const block = load("components/plate-ui/code-block-node.tsx", "\nexport { CodeBlockCombobox };").CodeBlockCombobox();
        assert(nodes(block).some((n) => n.props?.["aria-label"] === i18n.t("editor.Code language")));
        assertions++;
    }
    console.log(
        JSON.stringify({
            locales: 4,
            assertions,
            scope: "Actual component functions transpiled; hooks and primitive components mocked. No DOM or browser proof.",
        })
    );
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
