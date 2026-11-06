'use strict';

// Keep Node as PM2's child so service restarts cannot leave pnpm/dotenv descendants.
module.exports = {
  apps: [
    {
      name: 'backend',
      cwd: '/app/apps/backend',
      script: './dist/apps/backend/src/main.js',
      interpreter: 'node',
      node_args: ['--experimental-require-module'],
      exec_mode: 'fork',
      instances: 1,
      env: { NODE_ENV: 'production' },
    },
    {
      name: 'orchestrator',
      cwd: '/app/apps/orchestrator',
      script: './dist/apps/orchestrator/src/main.js',
      interpreter: 'node',
      node_args: ['--experimental-require-module'],
      exec_mode: 'fork',
      instances: 1,
      env: { NODE_ENV: 'production' },
    },
    {
      name: 'frontend',
      cwd: '/app/apps/frontend',
      script: '/app/node_modules/next/dist/bin/next',
      interpreter: 'node',
      args: ['start', '-p', '4200'],
      exec_mode: 'fork',
      instances: 1,
      env: { NODE_ENV: 'production' },
    },
  ],
};
