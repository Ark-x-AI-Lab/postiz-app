#!/usr/bin/env node
'use strict';

// Remove only Coolify's injected global env_file. Explicit service settings stay intact.
const EXPECTED = ['postiz', 'postiz-postgres', 'postiz-redis', 'temporal',
  'temporal-postgresql', 'temporal-elasticsearch', 'temporal-admin-tools', 'temporal-ui'];
const PROVIDER_KEYS = ['JWT_SECRET', 'FACEBOOK_APP_ID', 'FACEBOOK_APP_SECRET',
  'LINKEDIN_CLIENT_ID', 'LINKEDIN_CLIENT_SECRET', 'YOUTUBE_CLIENT_ID', 'YOUTUBE_CLIENT_SECRET'];

function prepare(raw, parse) {
  const data = parse(raw);
  if (!data || typeof data !== 'object' || !data.services
      || JSON.stringify(Object.keys(data.services).sort()) !== JSON.stringify([...EXPECTED].sort())) {
    throw new Error('Unexpected service set');
  }
  for (const [name, service] of Object.entries(data.services)) {
    if (!service || typeof service !== 'object') throw new Error('Invalid service');
    if (Object.hasOwn(service, 'env_file') && JSON.stringify(service.env_file) !== '[".env"]') {
      throw new Error('Unexpected service environment file');
    }
    const env = service.environment || {};
    if (typeof env !== 'object' || Array.isArray(env)) throw new Error('Environment must be a mapping');
    if (Object.hasOwn(env, 'ARKX_REGISTRY_TOKEN')) throw new Error('Registry token in service');
    if (name !== 'postiz' && PROVIDER_KEYS.some(key => Object.hasOwn(env, key))) {
      throw new Error('Provider settings in infrastructure');
    }
    delete service.env_file;
  }
  if (JSON.stringify(data.services.postiz.profiles) !== '["production"]'
      || JSON.stringify(data.services.postiz.command) !== '["sh","/app/deploy/arkx/runtime-start.sh"]') {
    throw new Error('Publisher startup guards changed');
  }
  return data;
}

module.exports = { prepare };
if (require.main === module || process.argv[1] === '--arkx-compose-stdin') {
  try {
    const fs = require('node:fs');
    const YAML = require('yaml');
    const raw = fs.readFileSync(0, 'utf8');
    if (Buffer.byteLength(raw) > 2 * 1024 * 1024) throw new Error('Compose too large');
    process.stdout.write(JSON.stringify(prepare(raw, YAML.parse)));
  } catch (_) {
    process.stderr.write('Compose preparation refused; configuration details withheld.\n');
    process.exitCode = 1;
  }
}
