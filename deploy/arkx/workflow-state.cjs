'use strict';
const crypto = require('node:crypto');

const technicalName = value => typeof value === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,80}$/.test(value) ? value : 'unclassified';
const identity = value => typeof value === 'string' && value.length > 0 && value.length <= 4096;

async function inspectWorkflows({ Connection, env = process.env, timeoutMs = 15000 }) {
  let connection, timer, finished = false;
  try {
    if (!env.TEMPORAL_ADDRESS?.trim() || !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) throw Error();
    const namespace = env.TEMPORAL_NAMESPACE?.trim() || 'default';
    const inspect = async () => {
      connection = await Connection.connect({ address: env.TEMPORAL_ADDRESS });
      if (finished) { await connection.close(); throw Error(); }
      const service = connection.workflowService;
      const listing = await service.listWorkflowExecutions({ namespace, query: "ExecutionStatus = 'Running'", pageSize: 100 });
      if (finished || !Array.isArray(listing.executions) || listing.executions.length > 100
        || listing.nextPageToken !== undefined && (!(listing.nextPageToken instanceof Uint8Array || Array.isArray(listing.nextPageToken)) || listing.nextPageToken.length)) throw Error();
      const workflows = [];
      for (const item of listing.executions) {
        const execution = item.execution;
        if (!identity(execution?.workflowId) || !identity(execution?.runId)) throw Error();
        const description = await service.describeWorkflowExecution({ namespace, execution });
        if (finished) throw Error();
        const activities = description.pendingActivities || [];
        if (!Array.isArray(activities) || activities.length > 1000) throw Error();
        const length = String(item.historyLength ?? 0);
        if (!/^\d{1,20}$/.test(length)) throw Error();
        workflows.push({
          identityHash: crypto.createHash('sha256').update(execution.workflowId + '\0' + execution.runId).digest('hex'),
          type: technicalName(item.type?.name),
          pendingActivities: activities.map(activity => ({
            type: technicalName(activity?.activityType?.name),
            state: Number.isInteger(activity?.state) && activity.state >= 0 && activity.state <= 5 ? activity.state : 'unknown',
          })),
          pendingWorkflowTask: !!description.pendingWorkflowTask,
          historyLength: length,
        });
      }
      return { ok: true, running: workflows.length, workflows };
    };
    return await Promise.race([inspect(), new Promise((_, reject) => { timer = setTimeout(() => reject(Error()), timeoutMs); })]);
  } catch {
    return { ok: false, error: 'Workflow metadata is unavailable.' };
  } finally {
    finished = true;
    clearTimeout(timer);
    if (connection) {
      let closeTimer;
      try { await Promise.race([connection.close(), new Promise(resolve => { closeTimer = setTimeout(resolve, 1000); })]); }
      catch {}
      finally { clearTimeout(closeTimer); }
    }
  }
}
module.exports = { inspectWorkflows };

if (require.main === module || process.argv[1] === '-') {
  (async () => {
    let result;
    try { result = await inspectWorkflows({ Connection: require('@temporalio/client').Connection }); }
    catch { result = { ok: false, error: 'Workflow metadata is unavailable.' }; }
    process.stdout.write(JSON.stringify(result) + '\n', () => process.exit(result.ok ? 0 : 1));
  })();
}
