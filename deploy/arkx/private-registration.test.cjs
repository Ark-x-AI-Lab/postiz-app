const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const root = path.resolve(__dirname, '../..');
let ts;
try { ts = require('typescript'); }
catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  ts = createRequire(path.resolve(root, '../arkx-social-studio/package.json'))('typescript');
}

// Only these real sources run. No application bootstrap or runtime config is loaded.
const sources = {
  service: 'apps/backend/src/services/auth/auth.service.ts',
  controller: 'apps/backend/src/api/routes/auth.controller.ts',
  registerDto: 'libraries/nestjs-libraries/src/dtos/auth/create.org.user.dto.ts',
  loginDto: 'libraries/nestjs-libraries/src/dtos/auth/login.user.dto.ts',
};
const compiled = Object.fromEntries(Object.entries(sources).map(([key, relative]) => {
  const output = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
    fileName: relative,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2017,
      module: ts.ModuleKind.CommonJS,
      experimentalDecorators: true,
      emitDecoratorMetadata: true,
      esModuleInterop: true,
    },
  });
  assert.equal(output.diagnostics.filter(item => item.category === ts.DiagnosticCategory.Error).length, 0);
  return [key, output.outputText];
}));

const Provider = Object.freeze(Object.fromEntries(
  ['LOCAL', 'GOOGLE', 'GENERIC', 'GITHUB', 'APPLE', 'FARCASTER', 'WALLET'].map(value => [value, value])
));
const privateResponse = Object.freeze({
  code: 'registration_disabled',
  message: 'This publisher is private. Contact your administrator.',
});

// Nest is substituted at the dependency boundary, including its public exception API.
class ForbiddenException extends Error {
  constructor(response) { super(response.message); this.response = response; }
  getStatus() { return 403; }
  getResponse() { return this.response; }
}

function fixture(options = {}, expected = []) {
  const remaining = [...expected], calls = [], violations = [], cache = {};
  function unexpected(label) {
    violations.push(label);
    throw new Error(`Unexpected fixture operation: ${label}`);
  }
  function step(label) {
    calls.push(label);
    if (remaining[0] !== label) return unexpected(label);
    remaining.shift();
  }
  function boundary(label, members) {
    return new Proxy(members, {
      get(target, key) {
        if (Object.hasOwn(target, key)) return target[key];
        return unexpected(`${label}.${String(key)}`);
      },
    });
  }
  const staff = () => ({ id: 'staff-fixture', activated: options.activated !== false, password: 'fixture-hash' });
  const provider = boundary('provider', {
    async getToken() {
      step('provider.exchange');
      if (options.exchangeFailure) throw new Error('Synthetic exchange rejected');
      return 'synthetic-provider-value';
    },
    async getUser() {
      step('provider.identity');
      return options.invalidIdentity ? null : { id: 'provider-fixture', email: 'cv.bouhlaoui+private-registration@gmail.com' };
    },
    async postRegistration() { step('provider.postRegistration'); },
  });
  const users = boundary('database.users', {
    async getUserByProvider() { step('user.providerLookup'); return options.known ? staff() : null; },
    async getUserByEmail() { step('user.localLookup'); return options.known ? staff() : null; },
  });
  const organizations = boundary('database.organizations', {
    async getCount() { step('organization.count'); return options.orgCount ?? 1; },
    async createOrgAndUser() { step('organization.create'); return { id: 'new-org-fixture', users: [{ user: staff() }] }; },
  });
  const email = boundary('email', {
    async sendEmailSync() { step('email.send'); },
    hasProvider() { step('email.config'); return true; },
  });
  const manager = boundary('providerManager', {
    getProvider() { step('provider.resolve'); return provider; },
  });
  const decorator = () => () => undefined;
  const common = {
    ForbiddenException,
    ...Object.fromEntries(['Injectable', 'Controller', 'Get', 'Post', 'Body', 'Req', 'Res', 'Param', 'Query', 'UseGuards']
      .map(key => [key, decorator])),
  };
  const dependencies = {
    '@nestjs/common': boundary('nest', common),
    '@prisma/client': { Provider },
    express: { Request: class {}, Response: class {} },
    'class-validator': Object.fromEntries(['IsDefined', 'IsEmail', 'IsString', 'MaxLength', 'MinLength', 'ValidateIf']
      .map(key => [key, decorator])),
    '@gitroom/nestjs-libraries/database/prisma/users/users.service': { UsersService: class {} },
    '@gitroom/nestjs-libraries/database/prisma/organizations/organization.service': { OrganizationService: class {} },
    '@gitroom/helpers/auth/auth.service': { AuthService: {
      comparePassword() { step('password.compare'); return options.validPassword !== false; },
      signJWT() { step('session.sign'); return 'synthetic-session-value'; },
    } },
    '@gitroom/backend/services/auth/providers/providers.manager': { AuthProviderManager: class {} },
    dayjs: () => unexpected('clock'),
    '@gitroom/nestjs-libraries/services/email.service': { EmailService: class {} },
    '@gitroom/nestjs-libraries/dtos/auth/forgot-return.password.dto': { ForgotReturnPasswordDto: class {} },
    '@gitroom/nestjs-libraries/dtos/auth/forgot.password.dto': { ForgotPasswordDto: class {} },
    '@gitroom/nestjs-libraries/dtos/auth/resend-activation.dto': { ResendActivationDto: class {} },
    '@gitroom/nestjs-libraries/newsletter/newsletter.service': {
      NewsletterService: { async register() { step('newsletter.register'); } },
    },
    '@nestjs/throttler': { Throttle: decorator },
    '@gitroom/nestjs-libraries/throttler/throttler.provider': { ThrottlerRealIpGuard: class {} },
    '@nestjs/swagger': { ApiTags: decorator },
    '@gitroom/helpers/subdomain/subdomain.management': { getCookieUrlFromDomain: () => undefined },
    'nestjs-real-ip': { RealIP: decorator },
    '@gitroom/nestjs-libraries/user/user.agent': { UserAgent: decorator },
    '@gitroom/nestjs-libraries/services/make.secure.id': { makeSecureId: () => unexpected('random') },
    '@sentry/nestjs': { metrics: { count() { step('metric.register'); } } },
    '@gitroom/nestjs-libraries/integrations/social/farcaster.provider': { FarcasterProvider: class {} },
  };
  const aliases = {
    '@gitroom/backend/services/auth/auth.service': 'service',
    '@gitroom/nestjs-libraries/dtos/auth/create.org.user.dto': 'registerDto',
    '@gitroom/nestjs-libraries/dtos/auth/login.user.dto': 'loginDto',
  };
  const env = Object.freeze({
    ...(options.flag === undefined ? {} : { DISABLE_REGISTRATION: options.flag }),
    ...(options.notSecured ? { NOT_SECURED: 'true' } : {}),
    FRONTEND_URL: 'https://private-registration.example',
  });
  // This isolated process object never reads or changes the host environment.
  const context = vm.createContext({
    process: Object.freeze({ env }),
    fetch: () => unexpected('network.fetch'),
    console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(key => [key, () => unexpected(`console.${key}`)])),
  }, { codeGeneration: { strings: false, wasm: false } });
  function load(key) {
    if (cache[key]) return cache[key].exports;
    const module = { exports: {} };
    cache[key] = module;
    const dependency = name => {
      if (aliases[name]) return load(aliases[name]);
      if (Object.hasOwn(dependencies, name)) return dependencies[name];
      return unexpected(`module.import.${name}`);
    };
    const factory = new vm.Script(`(function(require,module,exports) { 'use strict';\n${compiled[key]}\n})`, {
      filename: sources[key],
    }).runInContext(context);
    factory(dependency, module, module.exports);
    return module.exports;
  }
  const { AuthService } = load('service');
  const service = new AuthService(users, organizations, email, manager);
  const { AuthController } = load('controller');
  const controller = new AuthController(service, email);
  function body(kind, providerName = 'GOOGLE') {
    const Type = load(kind === 'register' ? 'registerDto' : 'loginDto')[kind === 'register' ? 'CreateOrgUserDto' : 'LoginUserDto'];
    return Object.assign(new Type(), {
      provider: providerName,
      email: 'cv.bouhlaoui+private-registration@gmail.com',
      password: providerName === 'LOCAL' ? 'fixture-password' : '',
      providerToken: providerName === 'LOCAL' ? '' : 'synthetic-provider-value',
      company: 'Private registration fixture',
      datafast_visitor_id: '',
    });
  }
  async function endpoint(kind, providerName) {
    const result = {};
    const response = {
      cookie(name) { step(`response.cookie.${name}`); return this; },
      header(name) { step(`response.header.${name}`); return this; },
      status(status) { result.status = status; return this; },
      json(value) { result.body = JSON.parse(JSON.stringify(value)); return this; },
      send(value) { result.body = value; return this; },
    };
    await controller[kind]({ cookies: {} }, body(kind, providerName), response, '127.0.0.1', 'fixture-agent');
    return result;
  }
  return {
    service, controller, body, endpoint,
    verify() { assert.deepEqual(violations, []); assert.deepEqual(remaining, []); },
    forbidBoundary: () => unexpected('self-check'),
    env,
  };
}

function scenario(name, options, expected, exercise) {
  test(name, async () => {
    const f = fixture(options, expected);
    await exercise(f);
    f.verify();
  });
}
const identitySteps = ['provider.resolve', 'provider.exchange', 'provider.identity', 'user.providerLookup'];
const routeIdentitySteps = ['provider.resolve', 'provider.identity', 'user.providerLookup'];
const state = 'login-fixture';
const callback = (f, provider = 'GOOGLE') => f.service.checkExists(provider, 'fixture-code', undefined, state, state);
const isPrivate = error => {
  assert.ok(error instanceof ForbiddenException);
  assert.equal(error.getStatus(), 403);
  assert.equal(JSON.stringify(error.getResponse()), JSON.stringify(privateResponse));
  return true;
};

test('fixture refuses unexpected operations and exposes an immutable synthetic environment', () => {
  const f = fixture({ flag: 'true' });
  assert.ok(Object.isFrozen(f.env));
  assert.throws(f.forbidBoundary, /Unexpected fixture operation/);
  assert.throws(f.verify, assert.AssertionError);
});

for (const orgCount of [0, 1]) {
  for (const provider of Object.values(Provider)) {
    scenario(`closed policy denies ${provider} with ${orgCount} organizations without counting`,
      { flag: 'true', orgCount }, [], async f => assert.equal(await f.service.canRegister(provider), false));
  }
}
for (const flag of [undefined, 'false', 'TRUE', '']) {
  for (const provider of ['LOCAL', 'GOOGLE', 'GENERIC']) {
    scenario(`non-closed policy preserves ${provider} for flag ${flag === undefined ? 'unset' : JSON.stringify(flag)}`,
      { flag }, [], async f => assert.equal(await f.service.canRegister(provider), true));
  }
}

for (const provider of ['GOOGLE', 'GENERIC']) {
  for (const orgCount of [0, 1]) {
    scenario(`closed ${provider} callback rejects unknown identity with ${orgCount} organizations`,
      { flag: 'true', orgCount }, identitySteps, async f => assert.rejects(callback(f, provider), isPrivate));
    scenario(`closed ${provider} callback keeps known staff with ${orgCount} organizations`,
      { flag: 'true', orgCount, known: true }, [...identitySteps, 'session.sign'], async f => {
        const result = await callback(f, provider);
        assert.deepEqual(Object.keys(result), ['jwt']);
        assert.ok(typeof result.jwt === 'string');
      });
  }
  for (const flag of [undefined, 'false']) {
    scenario(`open ${provider} callback keeps unknown registration flow (${flag ?? 'unset'})`,
      { flag }, identitySteps, async f => {
        const result = await callback(f, provider);
        assert.deepEqual(Object.keys(result), ['token']);
        assert.ok(typeof result.token === 'string');
      });
  }
}

for (const flag of ['true', 'false']) {
  for (const [name, suppliedState, cookie] of [['missing', undefined, state], ['mismatch', 'other-state', state], ['missing cookie', state, undefined]]) {
    scenario(`${flag} callback rejects ${name} state before provider lookup`, { flag }, [],
      async f => assert.rejects(f.service.checkExists('GOOGLE', 'fixture-code', undefined, suppliedState, cookie), /Invalid state/));
  }
  scenario(`${flag} callback rejects invalid identity before account lookup`, { flag, invalidIdentity: true },
    ['provider.resolve', 'provider.exchange', 'provider.identity'], async f => assert.rejects(callback(f), /Invalid user/));
  scenario(`${flag} callback preserves provider exchange rejection`, { flag, exchangeFailure: true },
    ['provider.resolve', 'provider.exchange'], async f => assert.rejects(callback(f), /Synthetic exchange rejected/));
}

for (const options of [{ notSecured: true }, { redirect: true }]) {
  scenario(`existing staff retain ${options.redirect ? 'mobile redirect' : 'NOT_SECURED'} state semantics`,
    { ...options, flag: 'true', known: true }, [...identitySteps, 'session.sign'], async f => {
      const result = await f.service.checkExists('GOOGLE', 'fixture-code', options.redirect ? 'https://private-registration.example/callback' : undefined);
      assert.deepEqual(Object.keys(result), ['jwt']);
    });
}

for (const orgCount of [0, 1]) {
  for (const provider of ['LOCAL', 'GOOGLE', 'GENERIC']) {
    for (const kind of ['register', 'login']) {
      const local = provider === 'LOCAL';
      scenario(`closed ${provider} direct ${kind} rejects unknown identity with ${orgCount} organizations`,
        { flag: 'true', orgCount }, local ? ['user.localLookup'] : routeIdentitySteps, async f => {
          const result = await f.endpoint(kind, provider);
          assert.equal(result.status, 400);
          assert.equal(result.body, local && kind === 'login' ? 'Invalid user name or password' : 'Registration is disabled');
        });
    }
  }
}

for (const provider of ['GOOGLE', 'GENERIC']) {
  for (const kind of ['register', 'login']) {
    const finish = kind === 'register' ? ['metric.register', 'response.header.onboarding'] : ['response.header.reload'];
    scenario(`closed ${provider} direct ${kind} keeps known staff`, { flag: 'true', known: true },
      [...routeIdentitySteps, 'session.sign', 'response.cookie.auth', ...finish], async f => {
        const result = await f.endpoint(kind, provider);
        assert.equal(result.status, 200);
        assert.deepEqual(result.body, { [kind]: true });
      });
  }
}
scenario('closed local staff login keeps password and session path', { flag: 'true', known: true },
  ['user.localLookup', 'password.compare', 'session.sign', 'response.cookie.auth', 'response.header.reload'],
  async f => assert.equal((await f.endpoint('login', 'LOCAL')).status, 200));
for (const [name, options, message] of [
  ['wrong password', { validPassword: false }, 'Invalid user name or password'],
  ['inactive account', { activated: false }, 'User is not activated'],
]) {
  scenario(`closed local staff rejects ${name}`, { flag: 'true', known: true, ...options },
    ['user.localLookup', 'password.compare'], async f => {
      const result = await f.endpoint('login', 'LOCAL');
      assert.equal(result.status, 400);
      assert.equal(result.body, message);
    });
}
scenario('closed local duplicate signup retains existing-account rejection', { flag: 'true', known: true },
  ['user.localLookup'], async f => {
    const result = await f.endpoint('register', 'LOCAL');
    assert.equal(result.status, 400);
    assert.equal(result.body, 'Email already exists');
  });

for (const flag of [undefined, 'false']) {
  for (const provider of ['GOOGLE', 'GENERIC']) {
    for (const kind of ['register', 'login']) {
      const finish = kind === 'register' ? ['metric.register', 'response.header.onboarding'] : ['response.header.reload'];
      scenario(`open ${provider} direct ${kind} retains creation path (${flag ?? 'unset'})`, { flag, orgCount: 0 },
        [...routeIdentitySteps, 'organization.create', 'newsletter.register', 'provider.postRegistration', 'session.sign', 'response.cookie.auth', ...finish],
        async f => assert.equal((await f.endpoint(kind, provider)).status, 200));
    }
  }
  scenario(`open LOCAL register retains activation path (${flag ?? 'unset'})`, { flag, orgCount: 0 },
    ['user.localLookup', 'organization.create', 'session.sign', 'email.send', 'email.config', 'response.header.activate'], async f => {
      const result = await f.endpoint('register', 'LOCAL');
      assert.equal(result.status, 200);
      assert.deepEqual(result.body, { activate: true });
    });
  scenario(`open LOCAL login does not create unknown account (${flag ?? 'unset'})`, { flag },
    ['user.localLookup'], async f => {
      const result = await f.endpoint('login', 'LOCAL');
      assert.equal(result.status, 400);
      assert.equal(result.body, 'Invalid user name or password');
    });
}
for (const kind of ['register', 'login']) {
  scenario(`closed direct ${kind} preserves invalid provider identity rejection`, { flag: 'true', invalidIdentity: true },
    ['provider.resolve', 'provider.identity'], async f => {
      const result = await f.endpoint(kind, 'GOOGLE');
      assert.equal(result.status, 400);
      assert.equal(result.body, 'Invalid provider token');
    });
}
