const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

// Exercise each component's actual picker callback with the installed picker's
// two event shapes. Rejected selections contain errors, not plainFiles.
for (const [name, exported] of [
    ['media-toolbar-button.tsx', 'MediaToolbarButton'],
    ['media-placeholder-node.tsx', 'PlaceholderElement'],
]) {
    test(`${name}: rejected, empty and multiple selections preserve upload ownership`, () => {
        let config;
        const inserted = [];
        const uploads = [];
        const registered = [];
        const editor = {
            getTransforms: () => ({ insert: { media: (files) => inserted.push(files) } }),
        };
        const react = {
            createElement: () => null,
            useState: () => [false, () => {}],
            useRef: () => ({ current: null }),
            useCallback: (callback) => callback,
            useEffect: () => {},
        };
        const exports = {};
        const source = fs.readFileSync(path.join(__dirname, '../src/components/plate-ui', name), 'utf8');
        vm.runInNewContext(ts.transpileModule(source, {
            compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
        }).outputText, {
            exports,
            DataTransfer: class {
                files = [];
                items = { add: (file) => this.files.push(file) };
            },
            require(id) {
                if (id === 'react') return react;
                if (id === 'use-file-picker') return { useFilePicker: (value) => { config = value; return {}; } };
                if (id === 'platejs/react') return {
                    useEditorRef: () => editor,
                    useEditorPlugin: () => ({ api: { placeholder: {
                        addUploadingFile: (...args) => registered.push(args),
                        removeUploadingFile: () => {},
                    } } }),
                    withHOC: (_provider, component) => component,
                };
                if (id === 'platejs') return { KEYS: { audio: 'audio', file: 'file', img: 'img', video: 'video' } };
                if (id === 'react-i18next') return { useTranslation: () => [(key) => key, { language: 'en' }] };
                if (id.endsWith('/uploadthing')) return { useUploadFile: () => ({ uploadFile: async (file) => uploads.push(file) }) };
                if (id === '@platejs/media/react') return { PlaceholderPlugin: {} };
                return new Proxy({}, { get: () => () => null });
            },
        }, { filename: name });
        exports[exported]({ nodeType: 'img', editor, element: { id: 'existing-placeholder', mediaType: 'img' } });
        config.onFilesSelected({ errors: [{ name: 'FileReaderError', reason: 'selection rejected' }] });
        config.onFilesSelected({ plainFiles: [], filesContent: [] });
        assert.equal(inserted.length + uploads.length, 0);
        const files = [new File(['image'], 'first.png', { type: 'image/png' }), new File(['image'], 'second.png', { type: 'image/png' })];
        config.onFilesSelected({ plainFiles: files, filesContent: [] });
        if (exported === 'MediaToolbarButton') {
            assert.equal(inserted.length, 1);
            assert.deepEqual(Array.from(inserted[0]), files);
            assert.equal(uploads.length, 0);
        } else {
            assert.equal(uploads[0], files[0]);
            assert.equal(registered[0][0], 'existing-placeholder');
            assert.equal(registered[0][1], files[0]);
            assert.deepEqual(Array.from(inserted[0]), files.slice(1));
        }
        assert.equal(config.readFilesContent, false, 'the upload owner reads binary files, not FileReader text parsing');
    });
}
