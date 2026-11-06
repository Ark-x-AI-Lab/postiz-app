#!/usr/bin/env node
'use strict';

// Direct backend routes (no nginx /api prefix). Only API_KEY is accepted on stdin.
// Session checks stay separate: AuthMiddleware can create a missing organization API key.
const dns = require('node:dns/promises');
const { randomUUID } = require('node:crypto');

function requireCondition(value) {
  if (!value) throw new Error('Smoke check refused');
}

function privateIPv4(address) {
  if (!/^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/.test(address)) return false;
  const octets = address.split('.').map(Number);
  if (octets.some((n) => n > 255) || octets.join('.') !== address) return false;
  const [a, b] = octets;
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

function configuration(env, allowHost, secrets) {
  requireCondition(typeof allowHost === 'string' && /^[a-z][a-z0-9-]{0,62}$/.test(allowHost));
  requireCondition(!['localhost', 'metadata', 'host-gateway', 'host-docker-internal'].includes(allowHost));
  requireCondition(typeof env.BASE_URL === 'string' && env.BASE_URL.length > 0);
  const base = new URL(env.BASE_URL);
  requireCondition(base.protocol === 'http:' && base.hostname === allowHost);
  requireCondition(!base.username && !base.password && base.pathname === '/' && !base.search && !base.hash);
  for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) {
    requireCondition(!env[key]);
  }
  requireCondition(!env.NODE_USE_ENV_PROXY || env.NODE_USE_ENV_PROXY === '0');
  const expected = env.EXPECTED_INTEGRATIONS ?? '2';
  requireCondition(typeof expected === 'string' && /^(0|[1-9][0-9]*)$/.test(expected));
  const expectedIntegrations = Number(expected);
  requireCondition(Number.isSafeInteger(expectedIntegrations));
  requireCondition(secrets && typeof secrets === 'object' && !Array.isArray(secrets));
  requireCondition(Object.keys(secrets).every((key) => key === 'API_KEY'));
  requireCondition(typeof secrets.API_KEY === 'string' && secrets.API_KEY.length > 0 && secrets.API_KEY.length <= 16384);
  requireCondition(!/[\x00-\x20\x7f]/.test(secrets.API_KEY));
  return { base, expectedIntegrations };
}

async function checkBackend({ env = process.env, allowHost, secrets,
  fetchImpl = globalThis.fetch, lookup = dns.lookup }) {
  const summary = {
    checks: {
      apiKeyValid: false,
      integrationCount: false,
      missingKeyRejected: false,
      invalidKeyRejected: false,
      registrationClosed: false,
    },
    integrations: { expected: null, actual: null },
    omitted: ['session-JWT-test', 'registration-write-test', 'publishing', 'live-provider-validity'],
  };
  try {
    const { base, expectedIntegrations } = configuration(env, allowHost, secrets);
    summary.integrations.expected = expectedIntegrations;
    // Resolve once and send to the checked numeric private IP, preventing DNS rebinding.
    const addresses = await lookup(allowHost, { all: true, family: 4 });
    requireCondition(addresses.length > 0 && addresses.every((row) => row.family === 4 && privateIPv4(row.address)));
    const pinned = new URL(base.origin);
    pinned.hostname = addresses[0].address;
    const headers = { Host: base.host };
    async function request(path, auth, verify) {
      let response;
      try {
        response = await fetchImpl(new URL(path, pinned).href, {
          method: 'GET', redirect: 'error', signal: AbortSignal.timeout(10000),
          headers: { ...headers, ...auth },
        });
        return await verify(response);
      } catch (_) {
        return false;
      } finally {
        try { await response?.body?.cancel(); } catch (_) {}
      }
    }
    const success = (response) => response.status >= 200 && response.status < 300;
    summary.checks.apiKeyValid = await request('/public/v1/is-connected',
      { Authorization: secrets.API_KEY }, async (response) => success(response) && (await response.json()).connected === true);
    summary.checks.integrationCount = await request('/public/v1/integrations',
      { Authorization: secrets.API_KEY }, async (response) => {
        if (!success(response)) return false;
        const integrations = await response.json();
        if (!Array.isArray(integrations)) return false;
        summary.integrations.actual = integrations.length;
        return integrations.length === expectedIntegrations;
      });
    summary.checks.missingKeyRejected = await request('/public/v1/is-connected', {},
      (response) => response.status === 401);
    let invalid = 'arkx-rehearsal-invalid-' + randomUUID();
    if (invalid === secrets.API_KEY) invalid += '-invalid';
    summary.checks.invalidKeyRejected = await request('/public/v1/is-connected',
      { Authorization: invalid }, (response) => response.status === 401);
    summary.checks.registrationClosed = await request('/auth/can-register', {},
      async (response) => success(response) && (await response.json()).register === false);
  } catch (_) {
    // Never emit request/response bodies, headers, URLs, credentials or raw errors.
  }
  const ok = Object.values(summary.checks).every((value) => value === true);
  return { summary, exitCode: ok ? 0 : 1 };
}

async function readSecrets(input) {
  let bytes = 0;
  const chunks = [];
  for await (const chunk of input) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += data.length;
    requireCondition(bytes <= 65536);
    chunks.push(data);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function main(argv = process.argv.slice(2), env = process.env,
  input = process.stdin, output = process.stdout) {
  requireCondition(argv.length === 2 && argv[0] === '--allow-host');
  const secrets = await readSecrets(input);
  const result = await checkBackend({ env, allowHost: argv[1], secrets });
  output.write(JSON.stringify(result.summary) + '\n');
  process.exitCode = result.exitCode;
}

module.exports = { checkBackend, main };
if (require.main === module) {
  main().catch(() => {
    process.stdout.write(JSON.stringify({ ok: false, omitted: ['session-JWT-test', 'registration-write-test', 'publishing', 'live-provider-validity'] }) + '\n');
    process.exitCode = 1;
  });
}
