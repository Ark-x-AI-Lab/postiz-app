#!/usr/bin/env node
'use strict';

// Remove Coolify's global env_file and infrastructure routing-network attachments.
const EXPECTED = ['postiz', 'postiz-postgres', 'postiz-redis', 'temporal',
  'temporal-postgresql', 'temporal-elasticsearch', 'temporal-admin-tools', 'temporal-ui'];
const PROVIDER_KEYS = ['JWT_SECRET', 'FACEBOOK_APP_ID', 'FACEBOOK_APP_SECRET',
  'LINKEDIN_CLIENT_ID', 'LINKEDIN_CLIENT_SECRET', 'YOUTUBE_CLIENT_ID', 'YOUTUBE_CLIENT_SECRET'];
const NETWORKS = {
  postiz: ['postiz-network', 'temporal-network', 'app-egress'],
  'postiz-postgres': ['postiz-network'],
  'postiz-redis': ['postiz-network'],
  temporal: ['temporal-network'],
  'temporal-postgresql': ['temporal-network'],
  'temporal-elasticsearch': ['temporal-network'],
  'temporal-admin-tools': ['temporal-network'],
  'temporal-ui': ['temporal-network'],
};

function prepareNetworks(data) {
  const networks = data.networks;
  if (!networks || typeof networks !== 'object' || Array.isArray(networks)) {
    throw new Error('Network definitions must be a mapping');
  }
  for (const name of NETWORKS.postiz) {
    const config = networks[name];
    if (!config || typeof config !== 'object' || config.external
        || (name !== 'app-egress' && config.internal !== true)) {
      throw new Error('Source network isolation changed');
    }
  }
  const extra = Object.keys(networks).filter(name => !NETWORKS.postiz.includes(name));
  if (extra.length > 1 || extra.some(name => !/^[a-z0-9]{24}$/.test(name)
      || networks[name]?.external !== true || networks[name]?.name !== name
      || Object.keys(networks[name]).some(key => !['name', 'external'].includes(key)))) {
    throw new Error('Unexpected external network');
  }
  const routing = extra[0];
  for (const [name, service] of Object.entries(data.services)) {
    if (Object.hasOwn(service, 'network_mode')) throw new Error('Unexpected network mode');
    const bindings = service?.networks;
    const keys = Array.isArray(bindings) ? bindings : Object.keys(bindings || {});
    if (!bindings || typeof bindings !== 'object' || new Set(keys).size !== keys.length
        || NETWORKS[name].some(key => !keys.includes(key))
        || keys.some(key => typeof key !== 'string'
          || (!NETWORKS[name].includes(key) && (!routing || key !== routing)))) {
      throw new Error('Unexpected service networks');
    }
    if (name !== 'postiz') {
      // Preserve the source bindings; only the recognized injected network is removed.
      if (Array.isArray(bindings)) service.networks = bindings.filter(key => key !== routing);
      else if (routing) delete bindings[routing];
    } else if (routing && !keys.includes(routing)) {
      throw new Error('Routing network is not attached to publisher');
    }
  }
}

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
  const temporal = data.services.temporal;
  // The pinned image supplies this file. A host bind can hide it on Coolify.
  if (temporal.image !== 'temporalio/auto-setup:1.28.1'
      || temporal.environment?.DYNAMIC_CONFIG_FILE_PATH !== 'config/dynamicconfig/docker.yaml'
      || (Object.hasOwn(temporal, 'volumes') && JSON.stringify(temporal.volumes) !== '[]')) {
    throw new Error('Temporal must use the pinned image dynamic configuration without mounts');
  }
  prepareNetworks(data);
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
