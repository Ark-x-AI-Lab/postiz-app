'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { inspectWorkflows } = require('./workflow-state.cjs');
const { readFileSync } = require('node:fs');
const { spawnSync } = require('node:child_process');
const env = { TEMPORAL_ADDRESS: 'private-endpoint', TEMPORAL_NAMESPACE: 'private-namespace' };
const item = { execution: { workflowId: 'private-workflow', runId: 'private-run' }, type: { name: 'maintenanceWorkflow' }, historyLength: 12, memo: { secret: 'private-payload' } };
function fixture(overrides = {}) {
  const calls = []; let closes = 0;
  const service = {
    listWorkflowExecutions: async args => { calls.push(['list', args]); return { executions: [item], nextPageToken: Buffer.alloc(0) }; },
    describeWorkflowExecution: async args => { calls.push(['describe', args]); return { pendingActivities: [{ activityType: { name: 'readStats' }, state: 1, input: 'private-payload' }], pendingWorkflowTask: {}, memo: 'private-payload' }; },
    ...overrides,
  };
  return { Connection: { connect: async () => ({ workflowService: service, close: async () => { closes++; } }) }, calls, closes: () => closes };
}
test('healthy read reports bounded technical metadata and no private identifiers or inputs', async () => {
  const f = fixture(); const r = await inspectWorkflows({ ...f, env });
  assert.equal(r.ok, true); assert.equal(r.running, 1); assert.equal(r.workflows[0].type, 'maintenanceWorkflow');
  assert.equal(r.workflows[0].pendingActivities.length, 1); assert.match(r.workflows[0].identityHash, /^[a-f0-9]{64}$/);
  assert.equal(f.closes(), 1); assert.deepEqual(f.calls.map(x => x[0]), ['list', 'describe']);
  for (const secret of ['private-endpoint', 'private-namespace', 'private-workflow', 'private-run', 'private-payload']) assert(!JSON.stringify(r).includes(secret));
  assert.equal(f.calls[0][1].query, "ExecutionStatus = 'Running'");
});
test('a complete empty response is zero; truncated or malformed responses refuse', async () => {
  for (const listing of [{ executions: [] }, { executions: [], nextPageToken: Buffer.alloc(0) }, { executions: [] , nextPageToken: Buffer.from('more') }, { executions: [], nextPageToken: {} }, { executions: [], nextPageToken: [] }, { executions: [], nextPageToken: [1] }, {}, { executions: {} }, { executions: Array(101).fill(item) }]) {
    const f = fixture({ listWorkflowExecutions: async () => listing }); const r = await inspectWorkflows({ ...f, env });
    assert.equal(r.ok, Array.isArray(listing.executions) && listing.executions.length === 0 && (listing.nextPageToken === undefined || (listing.nextPageToken instanceof Uint8Array || Array.isArray(listing.nextPageToken)) && !listing.nextPageToken.length));
    assert.equal(f.closes(), 1); if (!r.ok) assert.equal(r.running, undefined);
  }
});
test('unknown technical names are not echoed and unexpected activity states are explicit', async () => {
  const f = fixture({ listWorkflowExecutions: async () => ({ executions: [{ ...item, type: { name: 'private@example.invalid' } }] }), describeWorkflowExecution: async () => ({ pendingActivities: [{ activityType: { name: 'private@example.invalid' }, state: 99 }] }) });
  const r = await inspectWorkflows({ ...f, env }); assert.equal(r.workflows[0].type, 'unclassified'); assert.equal(r.workflows[0].pendingActivities[0].state, 'unknown'); assert(!JSON.stringify(r).includes('@'));
});
test('missing configuration and invalid deadlines refuse before connecting', async () => {
  for (const options of [{ env: {} }, { env, timeoutMs: 0 }, { env, timeoutMs: 60001 }]) {
    const Connection = { connect: () => assert.fail('must not connect') }; assert.equal((await inspectWorkflows({ Connection, ...options })).ok, false);
  }
});
test('provider failures and malformed identities or descriptions refuse without partial output', async () => {
  for (const overrides of [
    { listWorkflowExecutions: async () => { throw Error('private-payload'); } },
    { listWorkflowExecutions: async () => ({ executions: [{ ...item, execution: {} }] }) },
    { describeWorkflowExecution: async () => { throw Error('private-payload'); } },
    { describeWorkflowExecution: async () => ({ pendingActivities: 'private-payload' }) },
    { listWorkflowExecutions: async () => ({ executions: [{ ...item, historyLength: 'private-payload' }] }) },
  ]) { const f = fixture(overrides); const r = await inspectWorkflows({ ...f, env }); assert.deepEqual(r, { ok: false, error: 'Workflow metadata is unavailable.' }); assert.equal(f.closes(), 1); }
});
test('a stalled read hits its deadline and closes the connection', async () => {
  const f = fixture({ listWorkflowExecutions: () => new Promise(() => {}) }); const r = await inspectWorkflows({ ...f, env, timeoutMs: 5 }); assert.equal(r.ok, false); assert.equal(f.closes(), 1);
});
test('a connection completing after the deadline closes without making a metadata request', async () => {
  let resolve, calls = 0, closes = 0;
  const connection = { close: async () => { closes++; }, workflowService: { listWorkflowExecutions: async () => { calls++; } } };
  const Connection = { connect: () => new Promise(done => { resolve = done; }) };
  const r = await inspectWorkflows({ Connection, env, timeoutMs: 5 });
  assert.equal(r.ok, false); resolve(connection);
  await new Promise(done => setImmediate(done));
  assert.equal(calls, 0); assert.equal(closes, 1);
});
test('the actual stdin entry returns safe JSON and correct exit status with only synthetic dependencies', () => {
  const source = readFileSync(require.resolve('./workflow-state.cjs'), 'utf8');
  const setup = `const Module=require('node:module');const load=Module._load;Module._load=function(name,...args){if(name==='@temporalio/client')return {Connection:{connect:async()=>({workflowService:{listWorkflowExecutions:async()=>({executions:[]})},close:async()=>{}})}};if(name==='node:crypto')return load.call(this,name,...args);throw Error('Blocked import');};\n`;
  for (const configured of [true, false]) {
    const child = spawnSync(process.execPath, ['-'], { input: setup + source, encoding: 'utf8', timeout: 3000, env: configured ? { TEMPORAL_ADDRESS: 'private-endpoint' } : {} });
    assert.equal(child.error, undefined); assert.equal(child.status, configured ? 0 : 1); assert.equal(child.stderr, '');
    const result = JSON.parse(child.stdout); assert.equal(result.ok, configured); assert(!child.stdout.includes('private-endpoint'));
    if (configured) assert.equal(result.running, 0); else assert.equal(result.running, undefined);
  }
});
