#!/usr/bin/env python3
"""Root-only, local-Docker database rehearsal. No application startup is implemented.

Default stage is plan (no Docker access). All command output containing database or
runtime details stays in memory; errors deliberately omit subprocess output.
"""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import resource
import secrets
import signal
import fcntl
import socket
import stat
import ssl
import subprocess
import sys
import time

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
LABEL = "group.arkx.migration-rehearsal"
SCHEMA = "/app/libraries/nestjs-libraries/src/database/prisma/schema.prisma"
PRISMA = "/app/node_modules/prisma/build/index.js"
# Image contract for the pinned Debian/OpenSSL 3 candidate, not a runtime download/cache.
ENGINE_COMMIT = "173f8d54f8d52e692c7e27e72a88314ec7aeff60"
ENGINE_ENV = {
    "PRISMA_SCHEMA_ENGINE_BINARY": "/app/node_modules/@prisma/engines/schema-engine-debian-openssl-3.0.x",
    "PRISMA_QUERY_ENGINE_LIBRARY": "/app/node_modules/.prisma/client/libquery_engine-debian-openssl-3.0.x.so.node",
    "PRISMA_CLI_QUERY_ENGINE_TYPE": "library",
}
EXPECTED = {"User": 1, "Organization": 1, "Integration": 2, "Media": 0, "Post": 0}
ROLES = ("pg", "rollback", "tools", "egress", "dbprobe", "schema", "schema-repeat")
SAFE_IMAGE_ENV = {"PATH", "NODE_VERSION", "YARN_VERSION", "PNPM_HOME", "NEXT_PUBLIC_VERSION",
                  "LANG", "LC_ALL", "NODE_ENV", "HOME", "GOSU_VERSION", "PG_MAJOR",
                  "PG_VERSION", "PG_SHA256", "PGDATA", "LANGUAGE"}


class Refusal(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Refusal(message)


def emit(message):
    print(message, flush=True)


def command(argv, *, payload=None, stream=None, timeout=180, allow_failure=False):
    """No shell, caller environment, proxy, Docker context or raw error output."""
    try:
        result = subprocess.run(DOCKER + list(argv), input=payload, stdin=stream,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                                     "HOME": "/root", "LANG": "C.UTF-8"}, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Refusal("Docker operation failed/timed out; state unknown, retain targets and inspect before retry") from None
    if result.returncode != 0 and allow_failure:
        return b""
    need(result.returncode == 0, "Docker operation failed; output withheld; retain targets for private diagnosis")
    return result.stdout


def inspect(kind, name, field):
    return json.loads(command([kind, "inspect", "--format", "{{json " + field + "}}", name]))


def owned(labels, expected):
    return bool(labels) and all(labels.get(k) == v for k, v in expected.items())


def quote_ident(value):
    return '"' + value.replace('"', '""') + '"'


def names(args):
    return {role: args.prefix + "-" + role for role in ROLES}


def validate(args):
    need(re.fullmatch(r"arkx-migration-rehearsal-[a-z0-9][a-z0-9-]{3,24}", args.prefix),
         "Use a fresh prefix: arkx-migration-rehearsal- plus 4-25 lowercase letters/digits/hyphens")
    need(re.fullmatch(r"[a-f0-9]{32}", args.run_id), "run-id must be 32 lowercase hex characters, not a secret")
    if args.stage == "cleanup":
        need(args.confirm_cleanup == args.prefix, "Cleanup requires exact --confirm-cleanup prefix")
        return
    for value in (args.pg_image, args.candidate_image):
        need(value and re.fullmatch(r"sha256:[a-f0-9]{64}", value), "Supply full local immutable image IDs, never tags")
    need(args.backup_dir and args.dump_relative and args.dump_sha256, "Backup directory, relative dump and SHA256 required")
    need(re.fullmatch(r"[a-f0-9]{64}", args.dump_sha256), "Supply exact expected custom dump SHA256")
    base = Path(args.backup_dir)
    relative = Path(args.dump_relative)
    need(base.is_absolute() and base.is_dir() and not base.is_symlink(), "Backup must be an existing absolute directory")
    need(not relative.is_absolute() and relative.parts and all(x not in (".", "..") for x in relative.parts),
         "Dump must be a relative path below the backup directory")
    current = base
    for part in relative.parts:
        current = current / part
        need(not current.is_symlink(), "Symlink dump paths are refused")
    need(current.is_file() and os.path.commonpath([str(current.resolve()), str(base.resolve())]) == str(base.resolve()), "Dump must be a regular file inside backup")
    args.dump_path = current
    need(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", args.probe_host)
         and "." in args.probe_host and not args.probe_host.endswith(".local"), "Use a public DNS hostname only")


def file_sha256(source):
    digest = hashlib.sha256()
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def open_dump(args):
    """Hold one verified descriptor for both restores; never mount backup into a container."""
    fd = os.open(args.dump_path, os.O_RDONLY | os.O_NOFOLLOW)
    source = os.fdopen(fd, "rb")
    try:
        need(stat.S_ISREG(os.fstat(source.fileno()).st_mode), "Dump is not a regular file")
        need(source.read(5) == b"PGDMP", "Only PostgreSQL custom-format PGDMP dumps are accepted")
        source.seek(0)
        need(file_sha256(source) == args.dump_sha256, "Dump checksum mismatch")
        source.seek(0)
        return source
    except BaseException:
        source.close()
        raise


def host_https_baseline(host):
    """Choose one public IPv4 and prove that exact endpoint with verified TLS/SNI."""
    try:
        addresses = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
        ips = {ipaddress.IPv4Address(row[4][0]) for row in addresses}
        need(ips and all(ip.is_global for ip in ips), "Probe DNS must resolve only public IPv4 addresses")
        selected = str(min(ips, key=int))
        context = ssl.create_default_context()
        with socket.create_connection((selected, 443), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                tls.sendall(("HEAD / HTTP/1.1\r\nHost: " + host + "\r\nConnection: close\r\n\r\n").encode("ascii"))
                response = http.client.HTTPResponse(tls, method="HEAD")
                try:
                    response.begin()
                    need(200 <= response.status < 400, "Same-IP host HTTPS baseline not healthy")
                finally:
                    response.close()
        return selected
    except (OSError, ValueError, http.client.HTTPException):
        raise Refusal("Same-IP host HTTPS baseline failed; no Docker operation attempted") from None


def toolcheck_program():
    """Read/execute only bundled engine version checks; no CLI or fetch-engine import."""
    return """const fs = require('node:fs');
const pkg = require('/app/node_modules/prisma/package.json');
if (pkg.version !== '6.5.0') process.exit(41);
for (const p of ['/app/.env', '/app/prisma/.env', '/app/libraries/nestjs-libraries/src/database/prisma/.env']) {
  if (fs.existsSync(p)) process.exit(42);
}
if (!fs.existsSync('/app/node_modules/prisma/build/index.js')) process.exit(43);
if (!fs.existsSync('""" + SCHEMA + """')) process.exit(44);
const {spawnSync} = require('node:child_process');
const engines = """ + json.dumps(ENGINE_ENV) + """;
try {
  for (const key of ['PRISMA_SCHEMA_ENGINE_BINARY','PRISMA_QUERY_ENGINE_LIBRARY']) {
    const p = engines[key];
    if (!fs.realpathSync(p).startsWith('/app/node_modules/') || !fs.statSync(p).isFile()) throw new Error();
    fs.accessSync(p, fs.constants.R_OK | (key === 'PRISMA_SCHEMA_ENGINE_BINARY' ? fs.constants.X_OK : 0));
  }
  const version = spawnSync(engines.PRISMA_SCHEMA_ENGINE_BINARY, ['--version'], {timeout:10000, encoding:'utf8'});
  if (version.status !== 0 || !version.stdout.split(/\\s+/).includes(""" + json.dumps(ENGINE_COMMIT) + """)) throw new Error();
  if (require(engines.PRISMA_QUERY_ENGINE_LIBRARY).version().commit !== """ + json.dumps(ENGINE_COMMIT) + """) throw new Error();
} catch (_) {
  process.stdout.write('ENGINE_BUNDLE_INVALID');
  process.exit(71); // Fail closed before CLI startup; never invoke Prisma's downloader.
}
process.stdout.write('TOOLS_OK');"""


class Rehearsal:
    def __init__(self, args):
        self.args = args
        self.containers = names(args)
        self.network = args.prefix + "-net"
        self.volumes = [args.prefix + "-pg-data", args.prefix + "-rollback-data"]
        self.password = secrets.token_urlsafe(36)
        self.labels = {LABEL: args.run_id, LABEL + ".prefix": args.prefix}
        self.created = {}
        self.probe_ipv4 = None

    def label_args(self):
        result = []
        for key, value in self.labels.items():
            result += ["--label", key + "=" + value]
        return result

    def inventory(self, kind):
        if kind == "container":
            rows = command(["container", "ls", "-a", "--format", "{{.Names}}"])
        else:
            rows = command([kind, "ls", "--format", "{{.Name}}"])
        return set(rows.decode().splitlines())

    def fresh(self):
        for kind, targets in (("container", self.containers.values()), ("network", [self.network]),
                              ("volume", self.volumes)):
            need(not self.inventory(kind).intersection(targets), "Existing rehearsal target refused; choose a new prefix")
        for image in (self.args.pg_image, self.args.candidate_image):
            need(inspect("image", image, ".Id") == image, "Image ID mismatch")
            env = inspect("image", image, ".Config.Env") or []
            need(all(item.split("=", 1)[0] in SAFE_IMAGE_ENV for item in env),
                 "Image has unreviewed baked environment; do not import credentials into rehearsal")
        pg_env = inspect("image", self.args.pg_image, ".Config.Env") or []
        need(any(x == "PG_VERSION=14.5" or x.startswith("PG_VERSION=14.5-") for x in pg_env)
             and "PG_MAJOR=14" in pg_env and "PGDATA=/var/lib/postgresql/data" in pg_env,
             "PostgreSQL image must be exactly 14.5 with the standard data directory")
        emit("PASS: exact image IDs, PostgreSQL 14.5, environment allowlist and all target names fresh")

    def assert_network(self):
        need(inspect("network", self.network, ".Internal") is True, "Rehearsal network must remain internal")
        need(inspect("network", self.network, ".Driver") == "bridge", "Only isolated bridge networking allowed")
        need(inspect("network", self.network, ".Labels") == self.labels, "Network ownership mismatch")
        endpoints = inspect("network", self.network, ".Containers") or {}
        need(set(x["Name"] for x in endpoints.values()) <= set(self.containers.values()), "Unexpected network attachment")

    def extra_hosts(self, role):
        if role != "egress":
            return []
        need(self.probe_ipv4 is not None, "Egress requires the verified same-IP host baseline")
        ip = ipaddress.IPv4Address(self.probe_ipv4)
        need(ip.is_global, "Egress mapping must be the selected public IPv4")
        return [self.args.probe_host + ":" + str(ip)]

    def create(self, role, image, entrypoint, argv, env=None, volume=None):
        self.assert_network()
        name = self.containers[role]
        need(name not in self.inventory("container"), "Existing container refused")
        options = ["container", "create", "--name", name, "--pull=never", *self.label_args(),
                   "--network", self.network, "--restart=no", "--no-healthcheck", "--log-driver=none", "--cap-drop=ALL",
                   "--security-opt", "no-new-privileges", "--entrypoint", entrypoint]
        for mapping in self.extra_hosts(role):
            options += ["--add-host", mapping]
        if volume:
            need(volume in self.volumes and role in ("pg", "rollback"), "Only fresh PostgreSQL volumes allowed")
            options += ["--user", "postgres", "--mount", "type=volume,src=" + volume + ",dst=/var/lib/postgresql/data"]
        else:
            options += ["--user", "65534:65534", "--read-only", "--workdir", "/app", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"]
        payload = None
        if env:
            need(all("\n" not in v and "\r" not in v and "\x00" not in v for v in env.values()), "Unsafe environment value")
            options += ["--env-file", "/dev/stdin"]
            payload = "".join(k + "=" + v + "\n" for k, v in env.items()).encode()
        container_id = command([*options, image, *argv], payload=payload).decode().strip()
        need(re.fullmatch(r"[a-f0-9]{64}", container_id), "Unexpected container creation result; inspect before continuing")
        self.created[role] = container_id
        self.assert_container(role, image)
        return container_id

    def assert_container(self, role, image):
        target = self.created[role]
        need(inspect("container", target, ".Image") == image, "Container image mismatch")
        need(owned(inspect("container", target, ".Config.Labels"), self.labels), "Container ownership mismatch")
        need(not inspect("container", target, ".HostConfig.PortBindings"), "Published ports refused")
        need(not inspect("container", target, ".HostConfig.Privileged"), "Privileged container refused")
        need(inspect("container", target, ".HostConfig.NetworkMode") == self.network, "Network mode mismatch")
        need((inspect("container", target, ".HostConfig.ExtraHosts") or []) == self.extra_hosts(role),
             "Container ExtraHosts must exactly match its role; only egress may pin the verified public IPv4")
        networks = inspect("container", target, ".NetworkSettings.Networks") or {}
        need(set(networks) == {self.network}, "Container must have only the rehearsal network")
        mounts = inspect("container", target, ".Mounts") or []
        expected = self.volumes[0 if role == "pg" else 1] if role in ("pg", "rollback") else None
        data_mounts = [m for m in mounts if m.get("Type") != "tmpfs"]
        need((len(data_mounts) == 1 and data_mounts[0].get("Type") == "volume"
              and data_mounts[0].get("Name") == expected
              and data_mounts[0].get("Destination") == "/var/lib/postgresql/data") if expected else not data_mounts,
             "Unexpected mount; old volumes/bind mounts are forbidden")

    def node(self, role, program, env=None, argv=None):
        emit("CHECK: Node-only " + role + " (default image command is never invoked)")
        self.create(role, self.args.candidate_image, "node", argv or ["-e", program], env)
        self.assert_network()
        result = command(["container", "start", "--attach", self.created[role]], timeout=180)
        code = inspect("container", self.created[role], ".State.ExitCode")
        need(code == 0 and inspect("container", self.created[role], ".State.Status") == "exited",
             "Node-only " + role + " failed; private output withheld")
        return result

    def pg(self, role, volume):
        self.create(role, self.args.pg_image, "/usr/local/bin/docker-entrypoint.sh", ["postgres"],
                    {"POSTGRES_USER": "rehearsal", "POSTGRES_DB": "postiz", "POSTGRES_PASSWORD": self.password}, volume)
        command(["container", "start", self.created[role]])
        for _ in range(60):
            status = command(["container", "exec", self.created[role], "pg_isready", "-h", "127.0.0.1",
                              "-U", "rehearsal", "-d", "postiz"], timeout=10, allow_failure=True)
            if b"accepting connections" in status:
                break
            time.sleep(1)
        else:
            raise Refusal("New PostgreSQL readiness timed out")
        need(self.sql(role, "SHOW server_version;").startswith("14.5"), "Running PostgreSQL is not 14.5")

    def sql(self, role, query):
        return command(["container", "exec", self.created[role], "psql", "-X", "-A", "-t", "-q",
                        "--set", "ON_ERROR_STOP=1", "-U", "rehearsal", "-d", "postiz", "-c",
                        "SET default_transaction_read_only=on; " + query]).decode().strip()

    def restore(self, role, source):
        source.seek(0)
        need(file_sha256(source) == self.args.dump_sha256, "Backup changed before restore")
        source.seek(0)
        command(["container", "exec", "-i", self.created[role], "pg_restore", "--exit-on-error",
                 "--single-transaction", "--no-owner", "--no-privileges", "-U", "rehearsal", "-d", "postiz"],
                stream=source, timeout=900)
        source.seek(0)
        need(file_sha256(source) == self.args.dump_sha256, "Backup changed during restore")
        emit("PASS: logical custom-format restore into fresh " + role + " database")

    def columns(self, role):
        query = """SELECT COALESCE(json_object_agg(table_name, cols), '{}'::json)
FROM (SELECT c.table_name, json_agg(c.column_name ORDER BY c.ordinal_position) AS cols
FROM information_schema.columns c JOIN information_schema.tables t
ON c.table_schema=t.table_schema AND c.table_name=t.table_name
WHERE c.table_schema='public' AND t.table_type='BASE TABLE' GROUP BY c.table_name) x;"""
        return json.loads(self.sql(role, query))

    def snapshot(self, role, columns):
        result = {}
        for table, fields in sorted(columns.items()):
            selection = ",".join(quote_ident(field) for field in fields)
            query = ("SELECT json_build_array(count(*), md5(COALESCE(string_agg(md5(r::text), '' ORDER BY md5(r::text)), ''))) "
                     "FROM (SELECT " + selection + " FROM public." + quote_ident(table) + ") r;")
            result[table] = json.loads(self.sql(role, query))
        for table, count in EXPECTED.items():
            need(table in result and result[table][0] == count, "Expected aggregate mismatch: " + table)
        queue = int(self.sql(role, "SELECT count(*) FROM public.\"Post\" WHERE \"state\"='QUEUE';"))
        need(queue == 0, "Queued database posts differ from the supplied zero baseline")
        emit("PASS: User=1 Organization=1 Integration=2 Media=0 Post=0 queued-database-posts=0")
        return result

    def compare(self, expected, actual, label):
        need(expected == actual, label + " changed pre-existing row values or membership; fingerprints withheld")
        emit("PASS: " + label + " preserves all " + str(len(expected)) + " original tables and every original column value")

    def run(self):
        with open_dump(self.args) as source:
            # Before even Docker inspection: the container will use this exact endpoint.
            self.probe_ipv4 = host_https_baseline(self.args.probe_host)
            emit("PASS: same selected public IPv4 healthy over host HTTPS (verified TLS/SNI, no proxy)")
            self.fresh()
            command(["network", "create", "--driver", "bridge", "--internal", *self.label_args(), self.network])
            self.assert_network()
            for volume in self.volumes:
                need(volume not in self.inventory("volume"), "Volume appeared during preflight; refuse reuse")
                command(["volume", "create", *self.label_args(), volume])
                need(inspect("volume", volume, ".Labels") == self.labels, "Volume ownership mismatch")
            self.pg("pg", self.volumes[0])
            self.restore("pg", source)
            original_columns = self.columns("pg")
            before = self.snapshot("pg", original_columns)
            need(self.node("tools", toolcheck_program()) == b"TOOLS_OK",
                 "Candidate image needs matching bundled Prisma 6.5.0 schema/query engines; rebuild image, never download at runtime")
            # A response of ANY status means egress is possible. DNS/TLS errors do not prove isolation.
            probe = """(async () => {
try {
  await fetch('https://""" + self.args.probe_host + """/', {signal: AbortSignal.timeout(5000), redirect:'manual'});
  process.exitCode = 51;
} catch (e) {
  const codes = ['ENETUNREACH','EHOSTUNREACH','ETIMEDOUT','ECONNREFUSED'];
  if (e.name === 'TimeoutError' || codes.includes(e.cause?.code)) process.stdout.write('EGRESS_BLOCKED');
  else process.exitCode = 52;
}
})();"""
            need(self.node("egress", probe) == b"EGRESS_BLOCKED", "Outbound-blocked probe not proven")
            emit("PASS: candidate Node HTTPS public-host probe blocked on internal-only network")
            env = {"DATABASE_URL": "postgresql://rehearsal:" + self.password + "@" + self.containers["pg"] + ":5432/postiz",
                   "CHECKPOINT_DISABLE": "1", "PRISMA_HIDE_UPDATE_MESSAGE": "1", "ARKX_RUNTIME": "rehearsal",
                   "DISABLE_REGISTRATION": "true", **ENGINE_ENV}
            dbprobe = """const {PrismaClient} = require('/app/node_modules/@prisma/client');
(async () => { const p = new PrismaClient({log: []});
try { await p.$connect(); process.stdout.write('DB_ALLOWED'); }
catch (_) { process.exitCode=61; } finally { await p.$disconnect(); } })();"""
            need(self.node("dbprobe", dbprobe, env) == b"DB_ALLOWED", "Candidate database authentication/connectivity not proven")
            emit("PASS: candidate Node can authenticate to fresh database on the same isolated network")
            arguments = [PRISMA, "db", "push", "--schema", SCHEMA, "--skip-generate"]
            self.node("schema", "", env, arguments)
            self.compare(before, self.snapshot("pg", original_columns), "guarded candidate schema update")
            after_columns = self.columns("pg")
            after = self.snapshot("pg", after_columns)
            self.node("schema-repeat", "", env, arguments)
            need(after_columns == self.columns("pg"), "Repeated schema update changed column inventory")
            self.compare(after, self.snapshot("pg", after_columns), "repeated guarded schema update")
            source.seek(0)
            need(file_sha256(source) == self.args.dump_sha256,
                 "Backup changed during rehearsal; rollback source refused")
            self.pg("rollback", self.volumes[1])
            self.restore("rollback", source)
            need(original_columns == self.columns("rollback"), "Rollback column inventory differs from backup restore")
            self.compare(before, self.snapshot("rollback", original_columns), "independent original-backup database rollback")
        emit("PASS: DATABASE-ONLY rehearsal finished; no backend, orchestrator, old app or real send started")
        emit("GAP: full old-version rollback, login/JWT, uploads/Redis, Temporal workflows, live OAuth and cutover remain main-owned")
        emit("RETAINED: exact labeled containers/network and two fresh volumes; cleanup is a separate explicit stage")

    def cleanup(self):
        # Preflight ALL targets before removing any. No volume deletion is implemented.
        container_ids = []
        present = self.inventory("container")
        for name in self.containers.values():
            if name not in present:
                continue
            need(owned(inspect("container", name, ".Config.Labels"), self.labels), "Cleanup container ownership mismatch")
            need(inspect("container", name, ".HostConfig.NetworkMode") == self.network, "Cleanup network mismatch")
            container_ids.append((name, inspect("container", name, ".Id")))
        network_id = None
        if self.network in self.inventory("network"):
            self.assert_network()
            network_id = inspect("network", self.network, ".Id")
        for name, target_id in container_ids:
            need(inspect("container", name, ".Id") == target_id
                 and owned(inspect("container", target_id, ".Config.Labels"), self.labels), "Cleanup target changed")
            emit("CLEANUP exact container: " + name + " " + target_id)
            command(["container", "rm", "--force", target_id])
            need(name not in self.inventory("container"), "Container removal unproven")
        if network_id:
            need(inspect("network", self.network, ".Id") == network_id, "Cleanup network identity changed")
            self.assert_network()
            emit("CLEANUP exact network: " + self.network + " " + network_id)
            command(["network", "rm", network_id])
            need(self.network not in self.inventory("network"), "Network removal unproven")
        emit("PASS: exact owned container/network cleanup complete; both database volumes deliberately retained")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--stage", choices=("plan", "db-schema", "cleanup"), default="plan")
    result.add_argument("--prefix", required=True)
    result.add_argument("--run-id", required=True)
    result.add_argument("--pg-image")
    result.add_argument("--candidate-image")
    result.add_argument("--backup-dir")
    result.add_argument("--dump-relative")
    result.add_argument("--dump-sha256")
    result.add_argument("--probe-host", default="example.com")
    result.add_argument("--confirm-cleanup")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    validate(args)
    if args.stage == "plan":
        emit("PLAN ONLY: no Docker access; execution requires --stage db-schema on the authorized root server")
        emit("TARGETS: " + ", ".join(names(args).values()) + "; " + args.prefix + "-net; "
             + args.prefix + "-pg-data; " + args.prefix + "-rollback-data")
        emit("GAP: plan is not restored-data, network-isolation, migration or rollback execution proof")
        return
    need(sys.platform == "linux" and os.geteuid() == 0, "Execution/cleanup requires Linux root on the authorized Docker server")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # One owner per prefix. Never unlink the lock: another process may already hold its inode.
    lock_path = "/run/lock/" + args.prefix + ".lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal("Another operation owns this rehearsal prefix") from None
        rehearsal = Rehearsal(args)
        if args.stage == "cleanup":
            rehearsal.cleanup()
        else:
            rehearsal.run()


if __name__ == "__main__":
    try:
        main()
    except Refusal as exc:
        emit("FAIL: " + str(exc))
        emit("GAP: no automatic retry/cleanup; inspect exact labeled targets privately")
        sys.exit(1)
    except (OSError, ValueError, KeyError, KeyboardInterrupt):
        # Do not stringify arbitrary exceptions: they can contain connection URLs/records.
        emit("FAIL: rehearsal refused/interrupted/failed; details withheld. No automatic retry or cleanup. Inspect exact labeled targets privately.")
        sys.exit(1)
