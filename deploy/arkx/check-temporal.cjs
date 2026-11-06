#!/usr/bin/env node
'use strict';

// Match temporal.register.ts: IndexedValueType.TEXT = 1. Never overwrite a type.
const REQUIRED = ['organizationId', 'postId'];
const TEXT = 1;
const STATES = { 0: 'UNSPECIFIED', 1: 'REGISTERED', 2: 'DEPRECATED', 3: 'DELETED' };

function namespaceState(value) {
  if (Object.hasOwn(STATES, value)) return STATES[value];
  if (typeof value === 'string') {
    const name = value.replace(/^NAMESPACE_STATE_/, '');
    if (Object.values(STATES).includes(name)) return name;
  }
  return 'UNKNOWN';
}

function attributeMap(response) {
  const attributes = response?.customAttributes;
  if (!attributes || typeof attributes !== 'object' || Array.isArray(attributes)) {
    throw new Error('Invalid attribute response');
  }
  return attributes;
}

async function checkTemporal({ Connection, env = process.env, registerMissing = false }) {
  const summary = {
    namespaceState: 'UNKNOWN',
    textAttributes: { organizationId: false, postId: false },
  };
  let connection;
  let ok = false;
  try {
    const address = env.TEMPORAL_ADDRESS?.trim();
    if (!address) throw new Error('Missing Temporal address');
    const namespace = env.TEMPORAL_NAMESPACE?.trim() || 'default';
    connection = await Connection.connect({ address });
    const described = await connection.workflowService.describeNamespace({ namespace });
    summary.namespaceState = namespaceState(described?.namespaceInfo?.state);
    let attributes = attributeMap(
      await connection.operatorService.listSearchAttributes({ namespace })
    );
    const refreshSummary = () => {
      for (const name of REQUIRED) summary.textAttributes[name] = Object.hasOwn(attributes, name) && attributes[name] === TEXT;
    };
    refreshSummary();
    if (summary.namespaceState !== 'REGISTERED') throw new Error('Namespace not registered');
    // Validate every existing type before considering any registration.
    if (REQUIRED.some((name) => Object.hasOwn(attributes, name) && attributes[name] !== TEXT)) {
      throw new Error('Existing attribute has wrong type');
    }
    const missing = REQUIRED.filter((name) => !Object.hasOwn(attributes, name));
    if (missing.length && registerMissing) {
      await connection.operatorService.addSearchAttributes({
        namespace,
        searchAttributes: Object.fromEntries(missing.map((name) => [name, TEXT])),
      });
      attributes = attributeMap(
        await connection.operatorService.listSearchAttributes({ namespace })
      );
      refreshSummary();
    }
    ok = REQUIRED.every((name) => summary.textAttributes[name]);
  } catch (_) {
    // No provider errors, endpoints, namespace names or credentials in output.
    ok = false;
  } finally {
    if (connection) {
      try { await connection.close(); } catch (_) { ok = false; }
    }
  }
  return { summary, exitCode: ok ? 0 : 1 };
}

async function main(argv = process.argv.slice(2), env = process.env) {
  if (argv.some((arg) => arg !== '--register-missing') || argv.length > 1) {
    throw new Error('Invalid arguments');
  }
  const { Connection } = require('@temporalio/client');
  const result = await checkTemporal({
    Connection, env, registerMissing: argv.includes('--register-missing'),
  });
  process.stdout.write(JSON.stringify(result.summary) + '\n');
  process.exitCode = result.exitCode;
}

module.exports = { checkTemporal, main };
if (require.main === module) {
  main().catch(() => {
    process.stdout.write(JSON.stringify({
      namespaceState: 'UNKNOWN',
      textAttributes: { organizationId: false, postId: false },
    }) + '\n');
    process.exitCode = 1;
  });
}
