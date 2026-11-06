# Deployment findings

- Prisma 6.5.0 client generation does not prove the installed CLI schema engine is bundled. Build the installed CLI tools explicitly, then execute schema/client engine checks offline as an unprivileged user before image publication. Startup uses the pinned installed CLI, not `pnpm dlx`.
- Coolify generated per-service `env_file: .env` paths resolve from the project directory. Use the checkout root consistently; a nested deployment directory fails even when the CLI environment file is absolute.
- A candidate-only registry token must not be marked runtime: Coolify can inject its generated runtime environment file into every service. Retain registry credentials outside app/build injection and do not overwrite a shared server registry login.
- Upstream session middleware can create a missing organization API key during `GET /user/self`. Do not describe that generic session check as read-only; prove the required state or use a read-only database role for a separate preserved-session test.
- GNU `timeout` needs a bounded `--kill-after` to terminate a TERM-ignoring Docker client. Track and verify only the temporary helper’s exact container ID during cleanup.
- Compose profiles can be enabled by inherited `COMPOSE_PROFILES`, including a selected dotenv file. Infrastructure-only startup explicitly clears that environment value; publisher selection requires both an explicit activation flag and production runtime intent.
