const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const ts = require(process.env.POSTIZ_TYPESCRIPT_PATH || 'typescript');
const root = path.resolve(__dirname, '../../../..');
const sources = {
  shared: 'libraries/helpers/src/utils/oauth.diagnostics.ts',
  diagnostics: 'libraries/nestjs-libraries/src/integrations/linkedin.oauth.diagnostics.ts',
  abstract: 'libraries/nestjs-libraries/src/integrations/social.abstract.ts',
  filter: 'libraries/nestjs-libraries/src/integrations/integration.missing.scopes.ts',
  personal: 'libraries/nestjs-libraries/src/integrations/social/linkedin.provider.ts',
  page: 'libraries/nestjs-libraries/src/integrations/social/linkedin.page.provider.ts',
  start: 'apps/backend/src/api/routes/integrations.controller.ts',
  callback: 'apps/backend/src/api/routes/no.auth.integrations.controller.ts',
  service: 'libraries/nestjs-libraries/src/database/prisma/integrations/integration.service.ts',
  browser: 'apps/frontend/src/components/launches/continue.integration.tsx',
};
const sentinel = 'SYNTHETIC_PRIVATE_VALUE_7f94';
const uuid = '11111111-1111-4111-8111-111111111111';
const decorator = () => () => undefined;
class HttpException extends Error {
  constructor(response, status) {
    super(typeof response === 'string' ? response : response.message);
    this.response = response;
    this.status = status;
  }
  getStatus() { return this.status; }
  getResponse() { return this.response; }
}
let cache, store, logs, savedConsole, savedFetch, savedEnv, browserHooks;

// External modules are substituted; real controller/provider/service source runs.
// No database, Redis client, network dispatcher or app bootstrap is imported.
function load(key) {
  if (cache[key]) return cache[key];
  const file = path.join(root, sources[key]);
  const module = { exports: {} };
  const nativeRequire = createRequire(file);
  const dependency = (name) => {
    if (name.startsWith('node:') || ['fs', 'path', 'stream'].includes(name)) return nativeRequire(name);
    if (name === 'react') return {
      useCallback: (fn) => fn,
      useMemo: (fn) => fn(),
      useEffect: (fn) => browserHooks.effects.push(fn),
      useState: (initial) => {
        const index = browserHooks.index++;
        if (!(index in browserHooks.states)) browserHooks.states[index] = initial;
        return [browserHooks.states[index], (value) => { browserHooks.states[index] = value; }];
      },
    };
    if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === 'next/navigation') return { useRouter: () => ({ push: (value) => browserHooks.navigated.push(value) }) };
    if (name.endsWith('custom.fetch')) return { useFetch: () => browserHooks.fetch };
    if (name.endsWith('get.transation.service.client')) return { useT: () => (_, value) => value };
    if (name.endsWith('variable.context')) return { useVariables: () => ({}) };
    if (name.endsWith('set.timezone')) return { newDayjs: () => ({ utcOffset: () => 0 }) };
    if (name.endsWith('continue-provider/list')) return { continueProviderList: { 'linkedin-page': 'PagePicker' } };
    if (name.endsWith('helpers/use.integration')) return { IntegrationContext: { Provider: 'Context' } };
    if (name === '@nestjs/common') return new Proxy({ HttpException, HttpStatus: { NOT_FOUND: 404, BAD_REQUEST: 400 } }, { get: (obj, prop) => obj[prop] || decorator });
    if (name.includes('utils/oauth.diagnostics')) return load('shared');
    if (name.endsWith('linkedin.oauth.diagnostics')) return load('diagnostics');
    if (name.endsWith('social.abstract')) return load('abstract');
    if (name.endsWith('integration.missing.scopes')) return load('filter');
    if (name === 'axios') return { HttpStatusCode: { Conflict: 409, Ok: 200, Created: 201, BadRequest: 400, PreconditionFailed: 412, NotAcceptable: 406 } };
    if (name.endsWith('linkedin.provider')) return load('personal');
    if (name.endsWith('redis.service')) return { ioRedis: store };
    if (name === '@temporalio/activity') return { ApplicationFailure: Error };
    if (name.endsWith('make.secure.id')) return { makeSecureId: (length) => 's'.repeat(length) };
    if (name.endsWith('auth.service')) return { AuthService: { fixedEncryption: () => 'encrypted-fixture' } };
    if (name.endsWith('upload.factory')) return { UploadFactory: { createStorage: () => ({}) } };
    if (name === 'dayjs') return Object.assign(() => ({}), { extend() {} });
    return new Proxy({}, { get: (_, property) => property === '__esModule' ? false : decorator });
  };
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    fileName: file, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2021, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, experimentalDecorators: true, esModuleInterop: true },
  });
  assert.deepEqual(output.diagnostics.filter((d) => d.category === ts.DiagnosticCategory.Error), []);
  new Function('require', 'module', 'exports', output.outputText)(dependency, module, module.exports);
  cache[key] = module.exports;
  return module.exports;
}

beforeEach(() => {
  savedEnv = { ...process.env };
  delete process.env.LINKEDIN_OAUTH_DIAGNOSTICS;
  delete process.env.LINKEDIN_OAUTH_DIAGNOSTICS_TTL_SECONDS;
  delete process.env.LINKEDIN_OAUTH_DIAGNOSTICS_STORE_TIMEOUT_MS;
  delete process.env.STRIPE_PUBLISHABLE_KEY;
  process.env.FRONTEND_URL = 'https://fixture.invalid';
  process.env.LINKEDIN_CLIENT_ID = sentinel;
  process.env.LINKEDIN_CLIENT_SECRET = sentinel;
  const data = new Map();
  store = {
    data, writes: [], reads: [],
    async get(key) { this.reads.push(key); return data.get(key) || null; },
    async set(key, value, ...options) { this.writes.push({ key, value, options }); data.set(key, value); },
    async del(key) { data.delete(key); },
  };
  cache = {}; logs = [];
  browserHooks = { index: 0, states: [], effects: [], navigated: [], fetch: async () => { throw new Error('Browser network disabled'); } };
  savedConsole = Object.fromEntries(['info', 'log', 'warn', 'error'].map((name) => [name, console[name]]));
  for (const name of Object.keys(savedConsole)) console[name] = (...args) => logs.push(args);
  savedFetch = global.fetch;
  global.fetch = async () => { throw new Error('Outbound network disabled in diagnostic tests'); };
});
afterEach(() => {
  Object.assign(console, savedConsole);
  global.fetch = savedFetch;
  process.env = savedEnv;
});

function enable() { process.env.LINKEDIN_OAUTH_DIAGNOSTICS = 'true'; }
function records() { return logs.map((entry) => JSON.parse(entry[0])); }
function assertSafe(extra = '') {
  const output = JSON.stringify(logs) + extra;
  assert.equal(output.includes(sentinel), false);
  for (const record of records()) {
    assert.deepEqual(Object.keys(record).filter((key) => !['event', 'provider', 'correlationId', 'stage', 'outcome', 'error', 'providerCode', 'status', 'durationMs'].includes(key)), []);
    assert.match(record.correlationId, /^[0-9a-f-]{36}$/);
  }
}
function fixture(page = false) {
  const provider = page ? new (load('page').LinkedinPageProvider)() : new (load('personal').LinkedinProvider)();
  let created = 0;
  const integration = { id: 'fixture-integration', providerIdentifier: provider.identifier, token: sentinel, internalId: 'fixture-member', inBetweenSteps: page };
  const service = {
    async migrateIntegrationOnConnect() {},
    async createOrUpdateIntegration() { created++; return { ...integration, token: sentinel, refreshToken: sentinel, customInstanceDetails: sentinel }; },
    async getIntegrationById() { return integration; },
  };
  const manager = {
    getAllowedSocialsIntegrations: () => [provider.identifier],
    getSocialIntegration: () => provider,
    isDashboardCallable: () => true,
  };
  const start = new (load('start').IntegrationsController)(manager, service, {}, {});
  const callback = new (load('callback').NoAuthIntegrationsController)(manager, service, { startRefreshWorkflow: async () => {} }, { getOrgById: async () => ({ id: 'fixture-org' }) });
  const body = { state: 'ssssss', code: sentinel, timezone: '0' };
  return { provider, start, callback, body, integration, manager, created: () => created };
}
function providerResponses(provider, { status = 200, list = true, scope = true } = {}) {
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, method: options?.method, body: options?.body?.toString() });
    if (url.endsWith('/accessToken')) return Response.json({ access_token: sentinel, refresh_token: sentinel, expires_in: 3600, scope: scope ? provider.scopes.join(' ') : '' }, { status });
    if (url.endsWith('/userinfo')) return Response.json({ sub: 'fixture-member', name: sentinel, picture: sentinel });
    if (url.endsWith('/me')) return Response.json({ vanityName: sentinel });
    if (url.includes('organizationalEntityAcls')) return Response.json({ elements: list ? [{ organizationalTarget: 'urn:li:organization:fixture-page', role: 'ADMINISTRATOR', 'organizationalTarget~': { localizedName: sentinel, vanityName: sentinel } }] : [], message: sentinel }, { status });
    if (url.includes('/organizations/')) return Response.json({ id: 'fixture-page', localizedName: sentinel, vanityName: sentinel });
    throw new Error('Unexpected outbound request in fixture');
  };
  return calls;
}
async function startConnection(f) {
  return f.start.getIntegrationUrl(f.provider.identifier, undefined, undefined, undefined, undefined, { id: 'fixture-org' });
}

function pageService(f) {
  const service = Object.create(load('service').IntegrationService.prototype);
  const writes = [];
  service._integrationManager = f.manager;
  service._integrationRepository = {
    getIntegrationById: async (org, id) => org === 'fixture-org' && id === f.integration.id ? { ...f.integration } : undefined,
    checkForDeletedOnceAndUpdate: async () => {},
    updateIntegration: async (id, data) => { writes.push({ id, data }); },
  };
  return { service, writes };
}

test('default off and non-true flags leave results, exceptions and diagnostic storage unchanged', async () => {
  const { LinkedInOAuthDiagnostics } = load('diagnostics');
  for (const value of [undefined, 'false', 'TRUE', '1']) {
    if (value === undefined) delete process.env.LINKEDIN_OAUTH_DIAGNOSTICS;
    else process.env.LINKEDIN_OAUTH_DIAGNOSTICS = value;
    const diag = new LinkedInOAuthDiagnostics('linkedin');
    const result = { ok: true };
    assert.equal(await diag.resume(store, sentinel, 'callback', async () => { await diag.remember(store, sentinel); return result; }), result);
    const error = new Error(sentinel);
    await assert.rejects(diag.run('callback', async () => { throw error; }), (caught) => caught === error);
  }
  assert.deepEqual(logs, []);
  assert.deepEqual(store.writes, []);
  assert.deepEqual(store.reads, []);
});

test('browser/server schema rejects untrusted labels and strips secrets, URLs, headers, bodies and invalid numbers', () => {
  const { safeOAuthDiagnostic, reportOAuthDiagnostic } = load('shared');
  const diagnostic = { event: 'oauth_diagnostic', provider: 'linkedin', correlationId: uuid, stage: 'token_exchange', outcome: 'failure', error: 'http_error', status: 401, durationMs: 12 };
  const unsafe = { ...diagnostic, headers: { Authorization: sentinel }, url: `https://fixture.invalid?code=${sentinel}`, code: sentinel, state: sentinel, access_token: sentinel, person: sentinel, response: sentinel, stack: sentinel };
  assert.deepEqual(safeOAuthDiagnostic(unsafe), diagnostic);
  assert.match(reportOAuthDiagnostic(unsafe), /token_exchange.*11111111/);
  for (const property of ['event', 'provider', 'correlationId', 'stage', 'outcome']) assert.equal(safeOAuthDiagnostic({ ...unsafe, [property]: sentinel }), undefined);
  const stripped = safeOAuthDiagnostic({ ...diagnostic, error: sentinel, status: sentinel, durationMs: Infinity });
  assert.equal(stripped.error, undefined);
  assert.equal(stripped.status, undefined);
  assert.equal(stripped.durationMs, undefined);
  assertSafe();
});

test('personal OAuth controller to token/profile success preserves request payloads and safe browser correlation', async () => {
  const f = fixture();
  const calls = providerResponses(f.provider);
  const offStart = await startConnection(f);
  const offResult = await f.callback.connectSocialMedia('linkedin', f.body);
  const offCalls = [...calls];
  assert.equal(offStart.diagnostics, undefined);
  assert.equal(offResult.diagnostics, undefined);
  assert.deepEqual(logs, []);
  calls.length = 0;
  enable();
  const start = await startConnection(f);
  const result = await f.callback.connectSocialMedia('linkedin', f.body);
  assert.equal(start.url, offStart.url);
  assert.deepEqual(calls, offCalls);
  assert.equal(result.diagnostics.correlationId, start.diagnostics.correlationId);
  assert.equal(result.token, undefined);
  assert.equal(result.refreshToken, undefined);
  assert.equal(result.customInstanceDetails, undefined);
  load('shared').reportOAuthDiagnostic(start.diagnostics);
  load('shared').reportOAuthDiagnostic(result.diagnostics);
  assert.deepEqual(new Set(records().map((record) => record.correlationId)), new Set([start.diagnostics.correlationId]));
  for (const stage of ['connect', 'callback', 'token_exchange', 'scope_check', 'userinfo', 'member_profile']) assert.ok(records().some((r) => r.stage === stage && r.outcome === 'success'));
  assert.equal(records().find((r) => r.stage === 'token_exchange' && r.outcome === 'success').status, 200);
  assertSafe();
});

test('token rejection reports first failing stage/status and never echoes raw errors or token bodies', async () => {
  enable();
  const f = fixture();
  const calls = providerResponses(f.provider, { status: 401, scope: false });
  const start = await startConnection(f);
  await assert.rejects(f.callback.connectSocialMedia('linkedin', f.body), (error) => {
    const response = error.getResponse();
    assert.equal(response.diagnostics.stage, 'token_exchange');
    assert.equal(response.diagnostics.status, 401);
    assert.equal(response.diagnostics.correlationId, start.diagnostics.correlationId);
    assert.match(response.message, /token_exchange/);
    assertSafe(JSON.stringify(response));
    return true;
  });
  assert.equal(calls.length, 1);
  assert.equal(f.created(), 0);
});

test('missing scope remains blocked with an allowlisted scope failure', async () => {
  enable();
  const f = fixture();
  providerResponses(f.provider, { scope: false });
  await startConnection(f);
  await assert.rejects(f.callback.connectSocialMedia('linkedin', f.body), (error) => error.getResponse().diagnostics.error === 'scope_error');
  assert.equal(f.created(), 0);
  assertSafe();
});

test('Page callback, dashboard list and Page save reuse one reference through real service methods', async () => {
  enable();
  const f = fixture(true);
  providerResponses(f.provider);
  const start = await startConnection(f);
  const callback = await f.callback.connectSocialMedia('linkedin-page', f.body);
  assert.equal(callback.pages.length, 1);
  assert.equal(callback.diagnostics.correlationId, start.diagnostics.correlationId);
  const dashboard = await f.start.functionIntegration({ id: 'fixture-org' }, { id: f.integration.id, name: 'companies' });
  assert.equal(Array.isArray(dashboard), true);
  assert.equal(dashboard.length, 1);
  const Service = load('service').IntegrationService;
  const service = Object.create(Service.prototype);
  let writes = 0;
  service._integrationManager = f.manager;
  service._integrationRepository = {
    getIntegrationById: async () => f.integration,
    checkForDeletedOnceAndUpdate: async () => {},
    updateIntegration: async () => { writes++; },
  };
  const saved = await service.saveProviderPage('fixture-org', f.integration.id, { page: 'fixture-page', oauthDiagnosticId: callback.diagnostics.correlationId });
  assert.equal(saved.success, true);
  assert.equal(saved.diagnostics.correlationId, start.diagnostics.correlationId);
  assert.equal(writes, 1);
  assert.deepEqual(new Set(records().map((r) => r.correlationId)), new Set([start.diagnostics.correlationId]));
  for (const stage of ['page_list', 'page_details', 'page_save']) assert.ok(records().some((r) => r.stage === stage && r.outcome === 'success'));
  assertSafe();
});

test('Page list HTTP rejection is distinguishable from a healthy empty list', async () => {
  enable();
  for (const status of [403, 200]) {
    const f = fixture(true);
    providerResponses(f.provider, { list: false });
    const send = global.fetch;
    global.fetch = async (url, options) => url.includes('organizationalEntityAcls') ? Response.json({ elements: [], message: sentinel }, { status }) : send(url, options);
    await startConnection(f);
    const result = await f.callback.connectSocialMedia('linkedin-page', f.body);
    assert.equal(result.diagnostics.stage, 'page_list');
    assert.equal(result.diagnostics.error, status === 403 ? 'http_error' : 'empty_result');
    assert.match(load('shared').reportOAuthDiagnostic(result.diagnostics), /page_list/);
  }
  assertSafe();
});

test('network errors and invalid JSON contain no raw response, stack, URL or cause in diagnostics', async () => {
  enable();
  for (const kind of ['network_error', 'invalid_json']) {
    const diag = new (load('diagnostics').LinkedInOAuthDiagnostics)('linkedin');
    await assert.rejects(diag.run('callback', () => diag.requestJson('token_exchange', async () => {
      if (kind === 'network_error') throw Object.assign(new Error(sentinel), { cause: { token: sentinel } });
      return new Response(`<html>${sentinel}</html>`, { status: 502 });
    })), (error) => {
      assert.equal(error.getResponse().diagnostics.error, kind);
      assertSafe(JSON.stringify(error.getResponse()) + error.stack);
      return true;
    });
  }
});

test('LinkedIn raw callback exceptions stay out of logs when the flag is off', async () => {
  const f = fixture();
  await startConnection(f);
  global.fetch = async () => { throw new Error(sentinel); };
  await assert.rejects(f.callback.connectSocialMedia('linkedin', f.body), (error) => !error.message.includes(sentinel));
  assert.deepEqual(logs, []);
});

test('expired OAuth state is still blocked and diagnostic failure preserves a safe HTTP status', async () => {
  enable();
  const f = fixture();
  await assert.rejects(f.callback.connectSocialMedia('linkedin', f.body), (error) => {
    assert.equal(error.getStatus(), 400);
    assert.equal(error.getResponse().diagnostics.stage, 'callback');
    assertSafe(JSON.stringify(error.getResponse()));
    return true;
  });
  assert.equal(f.created(), 0);
});

test('storage errors/timeouts are bounded, never break OAuth and use configured TTL', async () => {
  enable();
  process.env.LINKEDIN_OAUTH_DIAGNOSTICS_TTL_SECONDS = '123';
  process.env.LINKEDIN_OAUTH_DIAGNOSTICS_STORE_TIMEOUT_MS = '5';
  const diag = new (load('diagnostics').LinkedInOAuthDiagnostics)('linkedin');
  await diag.run('connect', async () => { await diag.remember(store, sentinel); return { ok: true }; });
  assert.deepEqual(store.writes[0].options, ['EX', 123]);
  const unavailable = { get: async () => { throw new Error(sentinel); }, set: () => new Promise(() => {}) };
  const result = await diag.resume(unavailable, sentinel, 'callback', async () => {
    await diag.remember(unavailable, sentinel);
    return { ok: true };
  });
  assert.equal(result.ok, true);
  assert.equal(result.diagnostics.outcome, 'success');
  assert.ok(records().some((r) => r.error === 'correlation_storage_error'));
  assertSafe();
});

test('simultaneous attempts keep independent correlation contexts and discard poisoned stored IDs', async () => {
  enable();
  const diag = new (load('diagnostics').LinkedInOAuthDiagnostics)('linkedin');
  const attempts = await Promise.all([1, 2].map((n) => diag.run('connect', async () => {
    await new Promise((resolve) => setImmediate(resolve));
    await diag.remember(store, `fixture-${n}`);
    return { n };
  })));
  assert.notEqual(attempts[0].diagnostics.correlationId, attempts[1].diagnostics.correlationId);
  for (const attempt of attempts) assert.equal(store.data.get(`oauth-diagnostic:linkedin:fixture-${attempt.n}`), attempt.diagnostics.correlationId);
  store.data.set('oauth-diagnostic:linkedin:poison', sentinel);
  const result = await diag.resume(store, 'poison', 'callback', async () => ({ ok: true }));
  assert.notEqual(result.diagnostics.correlationId, sentinel);
  assertSafe();
});

test('only known OAuth provider error codes can reach logs, even on HTTP 200', async () => {
  enable();
  const diag = new (load('diagnostics').LinkedInOAuthDiagnostics)('linkedin');
  for (const code of ['invalid_grant', sentinel]) {
    const result = await diag.run('callback', async () => {
      await diag.requestJson('token_exchange', async () => Response.json({ error: code, error_description: sentinel, access_token: sentinel }));
      return { ok: false };
    });
    assert.equal(result.diagnostics.error, 'oauth_error');
    assert.equal(result.diagnostics.providerCode, code === sentinel ? undefined : code);
  }
  assertSafe();
});

function renderBrowser() {
  browserHooks.index = 0;
  return load('browser').ContinueIntegration({ provider: 'linkedin-page', searchParams: { code: sentinel, state: sentinel }, logged: true });
}
function findNode(node, type) {
  if (!node || typeof node !== 'object') return;
  if (node.type === type) return node;
  const children = node.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const result = findNode(child, type);
    if (result) return result;
  }
}
async function runBrowserEffect() {
  browserHooks.effects.shift()();
  await new Promise((resolve) => setImmediate(resolve));
}
const browserDiagnostic = (stage) => ({ event: 'oauth_diagnostic', provider: 'linkedin-page', correlationId: uuid, stage, outcome: 'failure', error: 'http_error', status: 403 });

test('callback screen shows only safe failure reference for Page-list and HTTP 400 failures', async () => {
  for (const status of [200, 400]) {
    browserHooks.states = []; browserHooks.effects = [];
    browserHooks.fetch = async () => Response.json({ message: sentinel, pages: [{ name: sentinel }], diagnostics: browserDiagnostic('page_list') }, { status });
    renderBrowser();
    await runBrowserEffect();
    assert.equal(browserHooks.states[0], true);
    assert.match(browserHooks.states[1], /page_list.*11111111/);
    assert.equal(browserHooks.states[2], null);
    const screen = JSON.stringify(renderBrowser());
    assert.equal(screen.includes(sentinel), false);
    assert.equal(screen.includes('page_list'), true);
  }
  assertSafe();
});

test('Page-save diagnostic failure leaves the picker and reaches the existing error display', async () => {
  browserHooks.states[2] = { integrationId: 'fixture-integration', pages: [], onboarding: false };
  browserHooks.fetch = async () => Response.json({ message: sentinel, diagnostics: browserDiagnostic('page_details') }, { status: 409 });
  const picker = findNode(renderBrowser(), 'PagePicker');
  assert.ok(picker);
  await picker.props.onSave({ page: 'fixture-page' });
  assert.equal(browserHooks.states[0], true);
  assert.equal(browserHooks.states[2], null);
  const screen = JSON.stringify(renderBrowser());
  assert.equal(screen.includes('page_details'), true);
  assert.equal(screen.includes(sentinel), false);
  assertSafe();
});

test('healthy browser Page selection and precondition handling keep their existing path', async () => {
  browserHooks.fetch = async () => Response.json({ inBetweenSteps: true, id: 'fixture-integration', pages: [{ id: 'fixture-page' }] });
  renderBrowser();
  await runBrowserEffect();
  assert.equal(browserHooks.states[0], false);
  assert.equal(browserHooks.states[2].integrationId, 'fixture-integration');
  assert.deepEqual(logs, []);
  browserHooks.states = []; browserHooks.effects = [];
  browserHooks.fetch = async () => Response.json({ diagnostics: browserDiagnostic('callback') }, { status: 412 });
  renderBrowser();
  await runBrowserEffect();
  assert.deepEqual(browserHooks.navigated, ['/launches?precondition=true']);
  assert.equal(browserHooks.states[0], false);
  assertSafe();
});

test('all changed TypeScript/React files pass the focused offline compilation gate', () => {
  const files = [...Object.values(sources),
    'libraries/nestjs-libraries/src/integrations/social/social.integrations.interface.ts',
    'apps/frontend/src/components/launches/add.provider.component.tsx',
    'apps/frontend/src/components/launches/launches.component.tsx',
  ];
  for (const name of files) {
    const output = ts.transpileModule(fs.readFileSync(path.join(root, name), 'utf8'), {
      fileName: name, reportDiagnostics: true,
      compilerOptions: { target: ts.ScriptTarget.ES2021, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, experimentalDecorators: true, emitDecoratorMetadata: true, esModuleInterop: true },
    });
    assert.deepEqual(output.diagnostics.filter((d) => d.category === ts.DiagnosticCategory.Error), [], name);
  }
});

test('two callbacks sharing one integration keep their own references through each browser Page picker and save', async () => {
  enable();
  const f = fixture(true);
  providerResponses(f.provider);
  const startA = await startConnection(f);
  const callbackA = await f.callback.connectSocialMedia('linkedin-page', f.body);
  const startB = await startConnection(f);
  const callbackB = await f.callback.connectSocialMedia('linkedin-page', f.body);
  const idA = startA.diagnostics.correlationId;
  const idB = startB.diagnostics.correlationId;
  assert.equal(callbackA.id, callbackB.id);
  assert.notEqual(idA, idB);
  assert.equal(store.data.get(`oauth-diagnostic:linkedin-page:${f.integration.id}`), idB);
  const { service, writes } = pageService(f);
  const saved = [];
  async function picker(callback) {
    browserHooks.states = []; browserHooks.effects = [];
    browserHooks.fetch = async () => Response.json(callback);
    renderBrowser();
    await runBrowserEffect();
    assert.equal(browserHooks.states[2].oauthDiagnosticId, callback.diagnostics.correlationId);
    browserHooks.fetch = async (_, options) => {
      const body = JSON.parse(options.body);
      assert.equal(body.oauthDiagnosticId, callback.diagnostics.correlationId);
      const result = await service.saveProviderPage('fixture-org', callback.id, body);
      saved.push({ expected: callback.diagnostics.correlationId, result });
      return Response.json(result);
    };
    return findNode(renderBrowser(), 'PagePicker').props.onSave;
  }
  const saveA = await picker(callbackA);
  const saveB = await picker(callbackB);
  await Promise.all([saveA({ page: 'fixture-page-A' }), saveB({ page: 'fixture-page-B' })]);
  assert.equal(writes.length, 2);
  assert.equal(saved.length, 2);
  for (const { expected, result } of saved) {
    assert.equal(result.success, true);
    assert.equal(result.diagnostics.correlationId, expected);
  }
  assert.deepEqual(new Set(saved.map(({ result }) => result.diagnostics.correlationId)), new Set([idA, idB]));
  // A's first save must not inherit B's integration-level dashboard mapping.
  assert.ok(records().some((r) => r.stage === 'page_details' && r.correlationId === idA));
  assert.ok(records().some((r) => r.stage === 'page_details' && r.correlationId === idB));
  assertSafe();
});

test('missing, unknown, cross-integration and poisoned Page-save bindings fall back to fresh references', async () => {
  enable();
  const f = fixture(true);
  providerResponses(f.provider);
  await startConnection(f);
  const callback = await f.callback.connectSocialMedia('linkedin-page', f.body);
  const original = callback.diagnostics.correlationId;
  const { service, writes } = pageService(f);
  const key = `oauth-diagnostic:linkedin-page:page-attempt:${f.integration.id}:${original}`;
  for (const value of [undefined, sentinel, { correlationId: original }, uuid]) {
    const result = await service.saveProviderPage('fixture-org', f.integration.id, { page: 'fixture-page', oauthDiagnosticId: value });
    assert.equal(result.success, true);
    assert.notEqual(result.diagnostics.correlationId, original);
    assert.notEqual(result.diagnostics.correlationId, uuid);
  }
  // A valid-looking reference bound to a different integration is not accepted.
  store.data.set(`oauth-diagnostic:linkedin-page:page-attempt:other-integration:${uuid}`, uuid);
  const other = await service.saveProviderPage('fixture-org', f.integration.id, { page: 'fixture-page', oauthDiagnosticId: uuid });
  assert.notEqual(other.diagnostics.correlationId, uuid);
  // A mismatched stored UUID must not be treated as validation of the echoed ID.
  for (const stored of [sentinel, uuid, undefined]) {
    if (stored === undefined) store.data.delete(key); else store.data.set(key, stored);
    const result = await service.saveProviderPage('fixture-org', f.integration.id, { page: 'fixture-page', oauthDiagnosticId: original });
    assert.equal(result.success, true);
    assert.notEqual(result.diagnostics.correlationId, original);
    assert.notEqual(result.diagnostics.correlationId, uuid);
  }
  assert.equal(writes.length, 8);
  assertSafe();
});

test('a first-tab Page-details failure keeps its callback reference after a second callback overwrites the dashboard reference', async () => {
  enable();
  const f = fixture(true);
  providerResponses(f.provider);
  await startConnection(f);
  const callbackA = await f.callback.connectSocialMedia('linkedin-page', f.body);
  await startConnection(f);
  const callbackB = await f.callback.connectSocialMedia('linkedin-page', f.body);
  const { service, writes } = pageService(f);
  const send = global.fetch;
  global.fetch = async (url, options) => {
    if (url.includes('/organizations/fixture-page-A?')) throw new Error(sentinel);
    return send(url, options);
  };
  await assert.rejects(service.saveProviderPage('fixture-org', f.integration.id, {
    page: 'fixture-page-A', oauthDiagnosticId: callbackA.diagnostics.correlationId,
  }), (error) => {
    const diagnostic = error.getResponse().diagnostics;
    assert.equal(diagnostic.stage, 'page_details');
    assert.equal(diagnostic.error, 'network_error');
    assert.equal(diagnostic.correlationId, callbackA.diagnostics.correlationId);
    assertSafe(JSON.stringify(error.getResponse()));
    return true;
  });
  assert.equal(writes.length, 0);
  const result = await service.saveProviderPage('fixture-org', f.integration.id, {
    page: 'fixture-page-B', oauthDiagnosticId: callbackB.diagnostics.correlationId,
  });
  assert.equal(result.success, true);
  assert.equal(result.diagnostics.correlationId, callbackB.diagnostics.correlationId);
  assert.equal(writes.length, 1);
  assertSafe();
});

test('a valid diagnostic Page binding grants no authorization and default-off Page saves use no diagnostic storage', async () => {
  enable();
  const f = fixture(true);
  providerResponses(f.provider);
  await startConnection(f);
  const callback = await f.callback.connectSocialMedia('linkedin-page', f.body);
  const { service, writes } = pageService(f);
  const data = { page: 'fixture-page', oauthDiagnosticId: callback.diagnostics.correlationId };
  const reads = store.reads.length;
  await assert.rejects(service.saveProviderPage('other-org', f.integration.id, data), (error) => error.getStatus() === 404);
  await assert.rejects(service.saveProviderPage('fixture-org', 'other-integration', data), (error) => error.getStatus() === 404);
  f.integration.inBetweenSteps = false;
  await assert.rejects(service.saveProviderPage('fixture-org', f.integration.id, data), (error) => error.getStatus() === 400);
  assert.equal(store.reads.length, reads);
  assert.equal(writes.length, 0);
  f.integration.inBetweenSteps = true;
  process.env.LINKEDIN_OAUTH_DIAGNOSTICS = 'false';
  logs = []; store.reads = []; store.writes = [];
  const result = await service.saveProviderPage('fixture-org', f.integration.id, data);
  assert.deepEqual(result, { success: true });
  assert.equal(writes.length, 1);
  assert.deepEqual(store.reads, []);
  assert.deepEqual(store.writes, []);
  assert.deepEqual(logs, []);
});
