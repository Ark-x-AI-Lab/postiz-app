const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.env.POSTIZ_TYPESCRIPT_PATH || 'typescript');
const root = path.resolve(__dirname, '../..');
const files = {
  register: 'apps/frontend/src/components/auth/register.tsx',
  login: 'apps/frontend/src/components/auth/login.tsx',
  page: 'apps/frontend/src/app/(app)/auth/login/page.tsx',
  fetch: 'libraries/helpers/src/utils/custom.fetch.func.ts',
  layout: 'apps/frontend/src/components/layout/layout.context.tsx',
};
const privateValue = 'SYNTHETIC_PRIVATE_VALUE';
let active, cache, query, fetchHook, variables, forms, browser, savedFetch;
const element = (type, props) => ({ type, props: props || {} });
const component = (name) => Object.assign((props) => element(name, props), { displayName: name });

// Execute the actual components and fetch/layout code. Only their dependencies
// are replaced. A deterministic hook host exercises effects, cleanup and replay
// without a DOM dependency, so this also runs in the offline production image.
class Host {
  constructor(fn, props = {}) { this.fn = fn; this.props = props; this.slots = []; this.pending = []; this.updates = 0; }
  render() {
    active = this; this.index = 0;
    this.tree = this.fn(this.props);
    active = undefined;
    for (const run of this.pending.splice(0)) run();
    return this.tree;
  }
  unmount() { for (const slot of this.slots) slot?.cleanup?.(); }
  replay() {
    for (const slot of this.slots) {
      if (slot?.effect) { slot.cleanup?.(); slot.cleanup = slot.effect(); }
    }
  }
}
const hooks = {
  useState(initial) {
    const host = active, index = host.index++;
    const slot = host.slots[index] ||= { value: typeof initial === 'function' ? initial() : initial };
    return [slot.value, (value) => { host.updates++; slot.value = typeof value === 'function' ? value(slot.value) : value; }];
  },
  useRef(initial) {
    const host = active, index = host.index++;
    return host.slots[index] ||= { current: initial };
  },
  useMemo(fn, deps) {
    const host = active, index = host.index++;
    const prior = host.slots[index];
    if (!prior || deps.some((value, i) => !Object.is(value, prior.deps[i]))) host.slots[index] = { value: fn(), deps };
    return host.slots[index].value;
  },
  useCallback(fn, deps) { return hooks.useMemo(() => fn, deps); },
  useEffect(effect, deps) {
    const host = active, index = host.index++;
    const prior = host.slots[index];
    if (!prior || deps.some((value, i) => !Object.is(value, prior.deps[i]))) {
      host.pending.push(() => {
        prior?.cleanup?.();
        host.slots[index] = { deps, effect, cleanup: effect() };
      });
    }
  },
};
function load(key) {
  if (cache[key]) return cache[key];
  const file = path.join(root, files[key]);
  const module = { exports: {} };
  const dependency = (name) => {
    if (name === 'react') return hooks;
    if (name === 'react/jsx-runtime') return { jsx: element, jsxs: element, Fragment: 'Fragment' };
    if (name === 'next/link') return { default: 'a', __esModule: true };
    if (name === 'next/dynamic') return { default: () => component('Wallet'), __esModule: true };
    if (name === 'next/navigation') return { useSearchParams: () => query, useRouter: () => ({ push() { throw new Error('Unexpected navigation'); } }) };
    if (name === 'react-hook-form') return {
      FormProvider: component('FormProvider'),
      useForm: (options) => {
        const form = { options, errors: [], register: (field) => ({ name: field }), handleSubmit: (fn) => fn, setError: (...args) => form.errors.push(args) };
        forms.push(form); return form;
      },
    };
    if (name.endsWith('/custom.fetch')) return { useFetch: () => fetchHook, FetchWrapperComponent: 'FetchWrapper' };
    if (name.endsWith('/auth/login')) return { Login: load('login').Login };
    if (name.endsWith('/variable.context')) return { useVariables: () => variables };
    if (name.endsWith('/is.general.server.side')) return { isGeneralServerSide: () => true };
    if (name.endsWith('/get.transation.service.client')) return { useT: () => (_, fallback) => fallback };
    if (name.endsWith('/return.url.component')) return { useReturnUrl: () => ({ getAndClear: () => undefined }) };
    if (name.endsWith('/delete.dialog')) return { deleteDialog: () => { throw new Error('Unexpected billing dialog'); } };
    if (name === '@hookform/resolvers/class-validator') return { classValidatorResolver: () => undefined };
    if (name.endsWith('/create.org.user.dto')) return { CreateOrgUserDto: class {} };
    if (name.endsWith('/login.user.dto')) return { LoginUserDto: class {} };
    if (name.endsWith('/use.fire.events')) return { useFireEvents: () => () => { throw new Error('Unexpected registration hook'); } };
    if (name.endsWith('/use.track')) return { useTrack: () => () => { throw new Error('Unexpected tracking'); } };
    if (name.endsWith('/track.enum')) return { TrackEnum: {} };
    if (name === 'react-use-cookie') return { default: () => [''], __esModule: true };
    if (name === 'clsx') return { default: (...items) => items.join(' '), __esModule: true };
    if (name.endsWith('/form/button')) return { Button: 'button' };
    if (name.endsWith('/form/input')) return { Input: 'input' };
    if (name.endsWith('/layout/loading')) return { LoadingComponent: 'Loading' };
    const provider = {
      'github.provider': 'GithubProvider', 'google.provider': 'GoogleProvider',
      'apple.provider': 'AppleProvider', 'oauth.provider': 'OauthProvider',
      'farcaster.provider': 'FarcasterProvider', 'wallet.ui.provider': 'WalletUiProvider',
    };
    for (const [suffix, exported] of Object.entries(provider)) if (name.endsWith('/' + suffix)) return { [exported]: exported };
    if (name.endsWith('/wallet.provider')) return { default: 'WalletProvider', __esModule: true };
    throw new Error('Unstubbed dependency: ' + name);
  };
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    fileName: file, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2021, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  assert.deepEqual(output.diagnostics.filter((d) => d.category === ts.DiagnosticCategory.Error), []);
  new Function('require', 'module', 'exports', 'window', 'document', output.outputText)(dependency, module, module.exports, browser, browser.document);
  return cache[key] = module.exports;
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...nodes(tree.props?.children)];
}
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join(' ');
  if (!tree || typeof tree === 'boolean') return '';
  if (typeof tree !== 'object') return String(tree);
  return text(tree.props?.children);
}
function assertFailure(host, isPrivate = false) {
  const tree = host.render();
  assert.equal(nodes(tree).some((n) => n.type === load('register').RegisterAfter || n.type === 'Loading' || n.type === 'input' || n.type === 'form'), false);
  assert.equal(nodes(tree).some((n) => n.props.href === '/auth/login'), true);
  assert.equal(text(tree).includes('This publisher is private.'), isPrivate);
  assert.equal(JSON.stringify(tree).includes(privateValue), false);
  assert.match(text(tree), isPrivate ? /Contact your administrator/ : /try again/);
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
function callback(search = 'provider=google&code=synthetic-code&state=synthetic-state') {
  query = new URLSearchParams(search);
  const host = new Host(load('register').Register);
  host.render(); return host;
}
beforeEach(() => {
  cache = {}; forms = []; query = new URLSearchParams();
  variables = { isGeneral: true, isSecured: true, backendUrl: 'https://fixture.invalid' };
  browser = { location: { href: 'https://fixture.invalid/auth', pathname: '/auth', reload: () => { browser.reloads++; } }, document: { cookie: '' }, reloads: 0 };
  fetchHook = async () => { throw new Error('Unexpected request'); };
  savedFetch = global.fetch;
  global.fetch = async () => { throw new Error('Outbound network disabled'); };
});
afterEach(() => { global.fetch = savedFetch; });

test('only registration_disabled HTTP 403 shows private copy; all failures hide form/token and allow retry', async (t) => {
  for (const scenario of [
    { name: 'closed', status: 403, body: { code: 'registration_disabled', message: privateValue, token: privateValue }, private: true },
    { name: 'invalid state', status: 403, body: { message: privateValue } },
    { name: 'wrong status', status: 400, body: { code: 'registration_disabled', message: privateValue } },
    { name: 'server error', status: 500, body: { message: privateValue, token: privateValue } },
    { name: 'malformed rejection JSON', status: 403, raw: privateValue },
    { name: 'malformed success JSON', status: 200, raw: privateValue },
    { name: 'empty success', status: 200, body: {} },
    { name: 'null success', status: 200, body: null },
    { name: 'non-string token', status: 200, body: { token: 123 } },
    { name: 'blank token', status: 200, body: { token: '  ' } },
    { name: 'network failure', network: true },
  ]) await t.test(scenario.name, async () => {
    fetchHook = async () => {
      if (scenario.network) throw new Error(privateValue);
      return scenario.raw ? new Response(scenario.raw, { status: scenario.status }) : Response.json(scenario.body, { status: scenario.status });
    };
    const host = callback(); await settle(); assertFailure(host, scenario.private); host.unmount();
  });
});

test('missing/unsupported provider, missing code and provider denial never exchange or render local signup', async () => {
  let calls = 0;
  fetchHook = async () => { calls++; throw new Error('Must not exchange'); };
  for (const search of ['provider=GOOGLE', 'code=synthetic-code', 'state=x', 'provider=&code=x', 'provider=LOCAL&code=x', 'provider=../GOOGLE&code=x', 'provider=unknown&code=x', 'provider=GOOGLE&code=x&error=access_denied', 'error=access_denied']) {
    const host = callback(search); await settle(); assertFailure(host); host.unmount();
  }
  assert.equal(calls, 0);
});

test('open local registration and successful provider token still render their real form', async () => {
  let host = callback('');
  assert.equal(host.tree.type, load('register').RegisterAfter);
  assert.deepEqual(host.tree.props, { token: '', provider: 'LOCAL' });
  const local = new Host(host.tree.type, host.tree.props); local.render();
  assert.deepEqual(nodes(local.tree).filter((n) => n.type === 'input').map((n) => n.props.name), ['email', 'password', 'company']);
  let request;
  fetchHook = async (...args) => { request = args; return Response.json({ token: privateValue }); };
  host = callback(); await settle(); host.render();
  assert.equal(host.tree.type, load('register').RegisterAfter);
  assert.deepEqual(host.tree.props, { token: privateValue, provider: 'GOOGLE' });
  assert.equal(request[0], '/auth/oauth/GOOGLE/exists');
  assert.equal(request[1].method, 'POST');
  assert.deepEqual(JSON.parse(request[1].body), { code: 'synthetic-code', state: 'synthetic-state' });
  const provider = new Host(host.tree.type, host.tree.props); provider.render();
  assert.deepEqual(nodes(provider.tree).filter((n) => n.type === 'input').map((n) => n.props.name), ['company']);
  assert.equal(forms.at(-1).options.defaultValues.providerToken, privateValue);
});

test('effect replay and rerender exchange a single-use authorization code once', async () => {
  const wait = deferred(); let calls = 0;
  fetchHook = () => { calls++; return wait.promise; };
  const host = callback(); host.replay(); host.render();
  assert.equal(calls, 1);
  wait.resolve(Response.json({ token: privateValue })); await settle(); host.render();
  assert.equal(host.tree.type, load('register').RegisterAfter);
  assert.equal(calls, 1);
});

test('unmount discards late callback state', async () => {
  const wait = deferred(); fetchHook = () => wait.promise;
  const host = callback(); host.unmount();
  wait.resolve(Response.json({ token: privateValue })); await settle();
  assert.equal(host.updates, 0);
});

test('query changes hide previous signup immediately and discard stale responses', async () => {
  const first = deferred(), second = deferred(); let calls = 0;
  fetchHook = () => (++calls === 1 ? first.promise : second.promise);
  const host = callback();
  query = new URLSearchParams('provider=GOOGLE&code=second-code&state=second-state'); host.render();
  first.resolve(Response.json({ token: privateValue })); await settle(); host.render();
  assert.equal(host.tree.type, 'Loading');
  second.resolve(Response.json({ code: 'registration_disabled' }, { status: 403 })); await settle(); assertFailure(host, true);
  assert.equal(calls, 2);
});

test('a settled provider form cannot leak into a later callback', async () => {
  fetchHook = async () => Response.json({ token: privateValue });
  const host = callback(); await settle(); host.render();
  assert.equal(host.tree.type, load('register').RegisterAfter);
  query = new URLSearchParams('provider=GOOGLE&code=next-code');
  fetchHook = () => new Promise(() => {}); host.render();
  assert.equal(host.tree.type, 'Loading'); host.unmount();
});

function sharedFetch() {
  const layout = new Host(load('layout').default, { children: 'Fixture' });
  const inner = layout.render();
  const context = new Host(inner.type, inner.props).render();
  return load('fetch').customFetch(context.props, undefined, undefined, variables.isSecured);
}

test('known Google login uses real shared fetch/layout cookies and reload; never displays failure or signup', async () => {
  fetchHook = sharedFetch(); let calls = 0;
  global.fetch = async (url, options) => {
    calls++; assert.equal(url, 'https://fixture.invalid/auth/oauth/GOOGLE/exists');
    assert.equal(options.credentials, 'include');
    return Response.json({ login: true }, { headers: { reload: 'true', auth: privateValue } });
  };
  const host = callback(); await settle(); host.render();
  assert.equal(browser.reloads, 1);
  assert.match(browser.document.cookie, /auth=SYNTHETIC_PRIVATE_VALUE/);
  assert.equal(calls, 1);
  assert.match(text(host.tree), /Signing you in/);
  assert.equal(/Unable|private/.test(text(host.tree)), false);
  assert.equal(nodes(host.tree).some((n) => n.type === 'Loading' || n.type === load('register').RegisterAfter), false);
});

test('HTTP 403 passes through actual shared fetch/layout and renders private callback message', async () => {
  fetchHook = sharedFetch();
  global.fetch = async () => Response.json({ code: 'registration_disabled', message: privateValue }, { status: 403 });
  const host = callback(); await settle(); assertFailure(host, true);
  assert.equal(browser.reloads, 0);
});

test('closed/default login hides only signup, keeps Google/local controls and posts LOCAL login', async () => {
  for (const props of [{}, { registrationEnabled: false }, { registrationEnabled: true }]) {
    const host = new Host(load('login').Login, props); host.render();
    assert.equal(nodes(host.tree).some((n) => n.props.href === '/auth'), props.registrationEnabled === true);
    assert.equal(nodes(host.tree).some((n) => n.type === 'GoogleProvider'), true);
    assert.deepEqual(nodes(host.tree).filter((n) => n.type === 'input').map((n) => n.props.name), ['email', 'password']);
    assert.equal(nodes(host.tree).some((n) => n.props.href === '/auth/forgot'), true);
  }
  const host = new Host(load('login').Login); host.render();
  fetchHook = sharedFetch();
  let payload;
  global.fetch = async (url, options) => {
    assert.equal(url, 'https://fixture.invalid/auth/login');
    payload = JSON.parse(options.body);
    return Response.json({ login: true }, { headers: { reload: 'true' } });
  };
  host.render();
  await nodes(host.tree).find((n) => n.type === 'form').props.onSubmit({ email: 'synthetic', password: 'synthetic', provider: 'GOOGLE' });
  assert.equal(payload.provider, 'LOCAL'); assert.equal(browser.reloads, 1);
});

test('actual server login page provides registration policy; client default stays closed', async () => {
  const previous = process.env.DISABLE_REGISTRATION;
  try {
    for (const flag of ['true', 'false', undefined]) {
      if (flag === undefined) delete process.env.DISABLE_REGISTRATION;
      else process.env.DISABLE_REGISTRATION = flag;
      const tree = await load('page').default();
      assert.equal(tree.type, load('login').Login);
      const host = new Host(tree.type, tree.props); host.render();
      assert.equal(nodes(host.tree).some((n) => n.props.href === '/auth'), flag !== 'true');
    }
  } finally {
    if (previous === undefined) delete process.env.DISABLE_REGISTRATION;
    else process.env.DISABLE_REGISTRATION = previous;
  }
});
