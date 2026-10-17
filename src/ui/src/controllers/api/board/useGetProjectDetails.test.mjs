import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const axios = require('axios');
const { QueryClient, isCancelledError } = require('@tanstack/react-query');
const source = readFileSync(new URL('./useGetProjectDetails.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function load(dock, { client, transport, writes = [] } = {}) {
    const models = Object.fromEntries(['Project', 'ProjectCard', 'ProjectColumn', 'InternalBotModel', 'ChatTemplateModel'].map(name => [name, {
        Model: { fromOne: value => { writes.push(value); return value; }, fromArray: value => value },
    }]));
    const dependencies = {
        '@langboard/core/constants': { Routing: { API: { BOARD: { DETAILS: '/board/{uid}/details' } } } },
        '@/core/helpers/Api': { api: { get: transport ?? (async () => ({ data: { project: {}, cards: [], columns: [], internal_bots: [], chat_templates: [] } })) } },
        '@/core/helpers/QueryMutation': { useQueryMutation: () => ({ query: (queryKey, queryFn, options) => client
            ? client.fetchQuery({ ...options, queryKey, queryFn })
            : () => queryFn({ signal: new AbortController().signal }) }) },
        '@/core/models': models,
        '@langboard/core/utils': { Utils: { String: { format: value => value } } },
        '@/controllers/api/board/refreshProjectColumnDock': { default: dock },
        '@/core/helpers/setupApiErrorHandler': { default: () => ({ handle: () => {} }) },
    };
    const exports = {};
    vm.runInNewContext(compiled, { exports, require: name => dependencies[name] });
    return exports.default({ uid: 'board', includeCards: false });
}

test('settings response completes while dock enrichment remains pending', async () => {
    const query = load(() => new Promise(() => {}));
    const result = await Promise.race([query(), new Promise((_, reject) => setTimeout(() => reject(new Error('Settings blocked on dock')), 100))]);
    assert.ok(result.project);
});

test('dock failure does not discard the authorized settings response', async () => {
    const result = await load(() => Promise.reject(new Error('Dock unavailable')))();
    assert.ok(result.project);
});

test('cancelled settings abort Axios and cannot write late models or start dock enrichment', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const writes = [];
    let release;
    let signal;
    let dockReads = 0;
    const transport = axios.create({ adapter: async config => {
        signal = config.signal;
        await new Promise(resolve => { release = resolve; });
        return { status: 200, statusText: 'OK', headers: {}, config,
            data: { project: {}, cards: [], columns: [], internal_bots: [], chat_templates: [] } };
    } });
    try {
        const pending = load(() => { dockReads++; return Promise.resolve(); }, {
            client, writes, transport: (url, options) => transport.get(url, options),
        }).catch(error => error);
        await client.cancelQueries({ queryKey: ['get-project-details-board', false], exact: true });
        release();
        assert.ok(isCancelledError(await pending));
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(signal?.aborted, true);
        assert.equal(writes.length, 0);
        assert.equal(dockReads, 0);
    } finally {
        release?.();
        client.clear();
    }
});
