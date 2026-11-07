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



# Separate current-data smoke. Legacy database rehearsal above is intentionally unchanged.
APP_SOURCE = "46d20f2929679c4db740b1f8b20b20be0bf8173c"
APP_IMAGE = "sha256:5d08a1d06f12542ffacd5345d369581f8d99d2b6194a40c55c722412631abe34"
APP_ACQUISITION = "db90e9b80773b160ea9682ba3fc6d41565a98dfe29bafefe82e58617bd5c0daa"
APP_PINS = {
    "current-backup-verified.json": APP_ACQUISITION,
    "temporal-read-config-basis.json": "bb310dd9900380f06874813c6dc7da3e6222201255622bbc4cf64d3fbbb7b262",
    "current-live-account-counts.json": "3f49ba95833110a5bdf9a127c1ab86fddf22d7e87f6336f4514fc668a66a33f0",
    "native-image-local-proof.json": "5537e79ed0b9f0f9049292e3a51b76bec45ce51437478e677142682ef1573056",
    "current-backup-plan.json": "a9493bf5ab7b24db445efdec8b69a056ab243a81f5a6b3efea38355070404bc6",
    # Immutable archived extraction evidence, NOT the mutable canonical g/h filenames.
    "restore-epochs/20261107g/current-restore-extraction-output.json": "0305bef6ecf3f1a652db9aff55e4ed881b24da82b4afd8bd0571e0f89a88bf03",
    "restore-epochs/20261107g/current-engines-reviewed-plan.json": "5aa89d6a9aa5ffb883ca1285cfd1ca2e1190ec8ea26fe6b928e2589a3de9a93b",
}
# Nonsecret stock metadata belongs only to these verified immutable images.
# Their digests bind the baked values; never allow these keys by role/tag globally.
APP_STOCK_IMAGE_ENV = {
    "sha256:0312984fbfeb2ff1d4382ecef45b50e7d65478a6a58cf12ce28d6de7a4142e45": frozenset({"REDIS_VERSION"}),
    "sha256:607d68caa111338d754771efb876c92dfcdae06d056e4530bb31cd0f37406e6a": frozenset({"TCTL_SHA", "TEMPORAL_SHA"}),
    "sha256:9a6443f55243f6acbfeb4a112d15eb3b9aac74bf25e0e39fa19b3ddd3a6879d0": frozenset({"ELASTIC_CONTAINER"}),
}
APP_CHECKS = {"check-backend.cjs": "106c1ec25e8afd877bc24b25d038e7f2a510312a5cd607fa8f6295cdf131f6cd",
              "check-temporal.cjs": "a4350c3097ef0c6bdf3b66dd3de01ce04fcf25fba7ba2b560363b314d626777b"}
APP_ROLES = ("pg14", "pg16", "redis", "es", "temporal", "backend")
APP_LIMITS = dict(zip(APP_ROLES + ("probe",), (160, 160, 64, 640, 256, 384, 64)))
APP_RESERVE, APP_DISK = 1024**3, 2*1024**3
APP_REQUIRED_MEMORY = sum(APP_LIMITS.values()) * 1024**2 + APP_RESERVE
APP_LABEL = "group.arkx.recovered-app-read"
APP_COUNTS = ("User", "Organization", "UserOrganization", "Integration", "Media", "Post", "queued", "published", "missingApiKeys")
APP_DATA = {"pg14": ("postiz-postgres", "/var/lib/postgresql/data"),
            "pg16": ("temporal-postgresql", "/var/lib/postgresql/data"),
            "redis": ("postiz-redis", "/data"), "es": ("temporal-elasticsearch", "/usr/share/elasticsearch/data")}
APP_HBA = b"local all all trust\nhost all all all trust\n"
APP_ES_STARTUP = 'set -eu; umask 077; mkdir -m 0700 /tmp/engine-config; cp -R /usr/share/elasticsearch/config/. /tmp/engine-config/; chmod 0700 /tmp/engine-config; test ! -e /tmp/engine-config/elasticsearch-plugins.yml; export ES_PATH_CONF=/tmp/engine-config; exec /usr/share/elasticsearch/bin/elasticsearch "$@"'
APP_ES_JAVA_OPTS = "-Xms256m -Xmx256m -Djava.io.tmpdir=/tmp -Djna.tmpdir=/tmp -XX:ActiveProcessorCount=1 -XX:-HeapDumpOnOutOfMemoryError -XX:ErrorFile=/tmp/jvm-error.log -Xlog:disable"
APP_TEMPORAL_ENTRY = "/usr/local/bin/temporal-server"
APP_TEMPORAL_DYNAMIC = "/etc/temporal/config/dynamicconfig/docker.yaml"
APP_PROC_ROOT, APP_CGROUP_ROOT = Path("/proc"), Path("/sys/fs/cgroup")


def app_need(value):
    need(value, "App-read refused; retain exact resources; private diagnostics withheld")


def app_canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def app_digest(value):
    return hashlib.sha256(app_canonical(value)).hexdigest()


def app_json(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            app_need(k not in result)
            result[k] = v
        return result
    app_need(len(raw) <= 1024**2)
    return json.loads(raw, object_pairs_hook=unique)


def app_read_file(path, expected=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        app_need(stat.S_ISREG(st.st_mode) and st.st_size <= 1024**2)
        with os.fdopen(os.dup(fd), "rb") as f:
            raw = f.read(1024**2 + 1)
        app_need(len(raw) <= 1024**2 and (expected is None or hashlib.sha256(raw).hexdigest() == expected))
        return raw
    finally:
        os.close(fd)


def app_inputs():
    root = Path(__file__).resolve().parents[2] / ".codex"
    values = {n: app_json(app_read_file(root / n, h)) for n, h in APP_PINS.items()}
    images = values["restore-epochs/20261107g/current-engines-reviewed-plan.json"]["plan"]["images"]
    images = {r: images[c] for r, (c, _) in APP_DATA.items()}
    images.update(temporal=values["current-backup-plan.json"]["plan"]["containers"]["temporal"]["image"], backend=APP_IMAGE, probe=APP_IMAGE)
    app_need(values["native-image-local-proof.json"]["id"] == APP_IMAGE
             and values["native-image-local-proof.json"]["revision"] == APP_SOURCE)
    checks = {n: app_read_file(Path(__file__).parent / n, h).decode() for n, h in APP_CHECKS.items()}
    trees = values["restore-epochs/20261107g/current-restore-extraction-output.json"]["components"]
    basis=values["temporal-read-config-basis.json"]
    app_need(basis["image_id"] == images["temporal"] and basis["entrypoint"] == APP_TEMPORAL_ENTRY
             and basis["source_sha256"] == "d4d641d90cf7bd9ca01b3e856b35c3e5ff6342ea68c746aa1fd69757d6196432"
             and basis["dynamic_config"]["filepath"] == "config/dynamicconfig/docker.yaml"
             and basis["persistence"]["numHistoryShards"] == 4 and basis["persistence"]["visibilityStore"] == "es-visibility")
    baseline=values["current-live-account-counts.json"]
    app_need(baseline["rows_exposed"] == 0)
    return images, trees, checks, baseline["counts"]


def app_plan(args, family=None):
    app_need(re.fullmatch(r"arkx-migration-rehearsal-[a-z0-9][a-z0-9-]{3,24}", args.prefix)
             and re.fullmatch(r"[a-f0-9]{32}", args.run_id))
    images, trees, _, baseline = app_inputs()
    plan = {"format": 1, "stage": "app-read", "source": APP_SOURCE, "images": images, "input_pins": APP_PINS,
            "check_pins": APP_CHECKS, "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "prefix": args.prefix, "run_id": args.run_id, "memory_mib": APP_LIMITS,
            "memory_reserve_bytes": APP_RESERVE, "memory_admission_bytes": APP_REQUIRED_MEMORY,
            "memory_credit": "lower sampled owned cgroup-v2 anon+shmem only; unchanged initial peak/reserve",
            "temporal_runtime": {"entrypoint":APP_TEMPORAL_ENTRY,"dynamic_config":APP_TEMPORAL_DYNAMIC,"history_shards":4,"visibility_store":"es-visibility"},
            "minimum_free_disk_bytes": APP_DISK, "network": args.prefix + "-net", "ports": [], "pull": False,
            "schema": False, "worker": False, "delete": False, "ready": family is not None,
            "count_baseline": {"provenance":"fresh-live-aggregate-not-acquisition", "counts":baseline},
            "limits": ["Media proof is mounted-byte readability, not HTTP serving or delivery.",
                       "No preserved cookie means session continuity is an explicit uncovered gap.",
                       "No full-workflow or whole-application recovery claim."]}
    if family is None:
        plan["needed"] = ["fresh externally prepared extraction family and receipt hashes", "pinned live aggregate baseline (not acquisition proof)",
                          "prepared secret-free Temporal configuration matching the pinned verified basis", "private runtime input on stdin"]
        return plan
    app_need(isinstance(family, dict) and set(family) == {"target", "plan_sha256", "result_sha256", "counts", "count_evidence_sha256", "config_sha256", "temporal_entrypoint", "api_integrations", "write_growth_budget_bytes"})
    target = family["target"]
    app_need(isinstance(target, str) and re.fullmatch(r"/var/lib/arkx-current-restore/arkx-current-restore-[a-z0-9-]{4,48}", target)
             and not re.fullmatch(r".*/arkx-current-restore-20261107[a-i]", target))
    for k in ("plan_sha256", "result_sha256", "count_evidence_sha256", "config_sha256"):
        app_need(isinstance(family[k], str) and re.fullmatch(r"[a-f0-9]{64}", family[k]))
    app_need(type(family["api_integrations"]) is int and 0 <= family["api_integrations"] <= 10**8)
    app_need(type(family["write_growth_budget_bytes"]) is int and 0 < family["write_growth_budget_bytes"] <= 8*1024**3)
    counts = family["counts"]
    app_need(isinstance(counts, dict) and set(counts) == set(APP_COUNTS)
             and all((k == "UserOrganization" and v is None) or (type(v) is int and 0 <= v <= 10**8) for k,v in counts.items()))
    for key, source in (("User","users"),("Organization","organizations"),("Integration","integrations"),("Media","media"),("Post","posts")):
        app_need(counts[key] == baseline[source])
    app_need(family["count_evidence_sha256"] == APP_PINS["current-live-account-counts.json"])
    app_need(counts["Post"] == counts["published"] == 3 and counts["queued"] == counts["missingApiKeys"] == 0)
    app_need(family["temporal_entrypoint"] == APP_TEMPORAL_ENTRY)
    plan.update(family=family, trees=trees, names={r: args.prefix + "-" + r for r in APP_ROLES + ("namespace", "api", "session", "media")})
    return plan


def app_memory_available():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            a = line.split(); app_need(len(a) == 3 and a[1].isdigit() and a[2] == "kB")
            return int(a[1]) * 1024
    raise Refusal("App-read available memory unproven")


def app_admit(available, free, allocated=0):
    app_need(type(available) is int and available >= APP_REQUIRED_MEMORY)
    app_need(type(free) is int and free >= APP_DISK + allocated)



def app_numeric(raw):
    text = raw.decode("ascii").strip()
    app_need(re.fullmatch(r"0|[1-9][0-9]{0,19}",text))
    value = int(text); app_need(value <= 2**64-1); return value


def app_memory_stat(raw):
    result = {}
    for line in raw.decode("ascii").splitlines():
        row=line.split();app_need(len(row)==2 and re.fullmatch(r"[a-z][a-z0-9_]{0,79}",row[0]) and row[0] not in result)
        result[row[0]]=app_numeric(row[1].encode())
    app_need(0 < len(result) <= 256 and {"anon","shmem"} <= set(result));return result


def app_owned_dir(path, uid=0, gid=0):
    """No symlink components; only the final process directory may be nonroot."""
    parts=app_absolute(str(path)).parts[1:];fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY)
    try:
        for index,name in enumerate(parts):
            st=os.fstat(fd);app_need(st.st_uid==0 and st.st_gid==0 and not st.st_mode & 0o022)
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
            st=os.fstat(fd);owner=(uid,gid) if index==len(parts)-1 else (0,0)
            app_need(stat.S_ISDIR(st.st_mode) and (st.st_uid,st.st_gid)==owner and not st.st_mode & 0o022)
        return fd
    except BaseException:
        os.close(fd);raise


def app_metadata(fd,name,uid=0,gid=0,limit=65536):
    app_need(isinstance(name,str) and re.fullmatch(r"[A-Za-z0-9_.]+",name) and name not in (".",".."))
    child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    try:
        st=os.fstat(child)
        app_need(stat.S_ISREG(st.st_mode) and (st.st_uid,st.st_gid)==(uid,gid) and not st.st_mode & 0o022)
        chunks=[];size=0
        while True:
            value=os.read(child,min(4096,limit+1-size))
            if not value:break
            chunks.append(value);size+=len(value);app_need(size<=limit)
        return b"".join(chunks)
    finally:os.close(child)


def app_process_start(raw,pid):
    text=raw.decode("ascii");end=text.rfind(")")
    app_need(text.startswith(str(pid)+" (") and end>len(str(pid))+2)
    fields=text[end+1:].split();app_need(20<=len(fields)<=64 and fields[0] in {"R","S","D","I"})
    start=app_numeric(fields[19].encode());app_need(start>0);return start


def app_members(raw):
    lines=raw.decode("ascii").splitlines()
    app_need(0<len(lines)<=128 and len(lines)==len(set(lines)))
    pids={app_numeric(v.encode()) for v in lines}
    app_need(len(pids)==len(lines) and all(0<p<=2**31-1 for p in pids));return pids


def app_process_identity(pid,membership,uid,gid):
    fd=app_owned_dir(APP_PROC_ROOT/str(pid),uid,gid)
    try:
        st=os.fstat(fd);start=app_process_start(app_metadata(fd,"stat",uid,gid,8192),pid)
        app_need(app_metadata(fd,"cgroup",uid,gid,4096).decode("ascii")=="0::"+membership+"\n")
        ns=os.open("ns",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        try:
            meta=os.fstat(ns);app_need((meta.st_uid,meta.st_gid)==(uid,gid))
            # Intentional kernel namespace-object stat, not a cgroup/proc-content symlink read.
            namespace=os.stat("pid",dir_fd=ns)
        finally:os.close(ns)
        app_need(app_process_start(app_metadata(fd,"stat",uid,gid,8192),pid)==start)
        return st.st_dev,st.st_ino,start,namespace.st_dev,namespace.st_ino
    finally:os.close(fd)


def app_absolute(value):
    app_need(isinstance(value, str) and value.startswith("/") and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    p = Path(value); app_need(str(p) == value and ".." not in p.parts)
    return p


def app_open_dir(path):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in app_absolute(str(path)).parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd); raise


def app_mount_id(fd):
    rows = [line.split()[1] for line in Path("/proc/self/fdinfo/" + str(fd)).read_text().splitlines() if line.startswith("mnt_id:")]
    app_need(len(rows) == 1); return rows[0]


def app_tree(root):
    rows, links = {}, {}; mount = app_mount_id(root)
    def walk(fd, prefix):
        app_need(app_mount_id(fd) == mount and len(rows) <= 1000000)
        st = os.fstat(fd); app_need(stat.S_ISDIR(st.st_mode))
        rows[prefix[:-1] or "."] = ["dir", st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)]
        for name in sorted(os.listdir(fd)):
            app_need(not any(ord(c) < 32 or ord(c) == 127 for c in name))
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                st = os.fstat(child); app_need(app_mount_id(child) == mount)
                if stat.S_ISDIR(st.st_mode): walk(child, prefix + name + "/")
                else:
                    app_need(stat.S_ISREG(st.st_mode))
                    with os.fdopen(os.dup(child), "rb") as f: checksum = file_sha256(f)
                    rows[prefix + name] = ["file", st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode), st.st_size, checksum]
                    key = st.st_dev, st.st_ino
                    count, size, expected = links.get(key, (0, st.st_size, st.st_nlink)); links[key] = count + 1, size, expected
            finally: os.close(child)
    walk(root, "")
    app_need(all(n == expected for n, _, expected in links.values()))
    return rows, {"entries": len(rows), "expanded_bytes": sum(v[1] for v in links.values()), "tree_sha256": app_digest(rows)}


def app_mounts():
    own, host = os.stat("/proc/self/ns/mnt"), os.stat("/proc/1/ns/mnt")
    app_need((own.st_dev, own.st_ino) == (host.st_dev, host.st_ino))
    with open("/proc/self/mountinfo", "rb") as source:
        raw = source.read(1024**2 + 1)
    app_need(len(raw) <= 1024**2); rows = []; seen = set()
    for line in raw.decode().splitlines():
        a = line.split(); app_need(len(a) >= 10 and "-" in a)
        sep = a.index("-"); app_need(sep >= 6 and len(a) == sep + 4 and re.fullmatch(r"\d+:\d+", a[2]) and a[0] not in seen)
        seen.add(a[0])
        def decode(v):
            v = re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m[1], 8)), v)
            app_need("\\" not in v); return app_absolute(v)
        if a[sep + 1] == "nsfs": app_need(re.fullmatch(r"net:\[\d+\]", a[3])); base = a[3]
        else: base = decode(a[3])
        rows.append((a[2], base, decode(a[4])))
    app_need(0 < len(rows) <= 4096 and not {d for d,r,_ in rows if isinstance(r,str)} & {d for d,r,_ in rows if isinstance(r,Path)}); return rows


def app_physical(path, mounts):
    candidates = [r for r in mounts if path == r[2] or r[2] in path.parents]
    app_need(candidates and len({r[2] for r in candidates}) == len(candidates))
    device, root, point = max(candidates, key=lambda r: len(r[2].parts)); app_need(isinstance(root, Path))
    return device, root / path.relative_to(point)


def app_overlap(a, b):
    return a == b or a in b.parents or b in a.parents


class AppRead:
    def __init__(self, plan, private, call=command):
        self.plan, self.private, self.call = plan, private, call
        self.ids, self.specs, self.handles, self.identity = {}, {}, {}, {}
        self.attempted, self.cancelled = [], False
        self.started = set()
        self.labels = {APP_LABEL: plan["run_id"], APP_LABEL + ".plan": app_digest(plan)}
        self.events, self.journal = [], None
        self.stopped, self.passed = {}, {}
        self.network_created, self.image_env, self.image_labels = False, {}, {}

    def event(self, value):
        self.events.append(value)
        if self.journal is not None:
            data = app_canonical(value) + b"\n"
            app_need(os.write(self.journal, data) == len(data)); os.fsync(self.journal)

    def record_refusal(self,error,phase):
        # Fixed journal schema; never format exceptions or inspect frame locals/arguments.
        if phase not in ("execution","shutdown"):return
        try:
            roots=list(globals().values())+list(AppRead.__dict__.values())
            codes={}
            for value in roots[:256]:
                code=getattr(value,"__code__",None)
                if (code is not None and code.co_filename==__file__ and code.co_name not in {"need","app_need"}
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}",code.co_name)):
                    codes[code]=code.co_name
            name,line=None,None;frame=error.__traceback__
            for _ in range(32):
                if frame is None:break
                code=frame.tb_frame.f_code
                if code in codes and frame.tb_lineno in {n for _,_,n in code.co_lines() if n is not None}:
                    name,line=codes[code],frame.tb_lineno
                frame=frame.tb_next
            self.event({"state":"refused","phase":phase,"source_function":name,"source_line":line})
        except (Exception,KeyboardInterrupt):
            pass  # An unavailable diagnostic journal must not mask refusal or interrupt shutdown.

    def checked_execute(self):
        try:return self.execute()
        except (Exception,KeyboardInterrupt) as error:
            self.record_refusal(error,"execution")
            raise Refusal("App-read refused; sanitized technical location is in the retained journal when available") from None

    def check_cancel(self):
        app_need(not self.cancelled)

    def inspect(self, kind, target):
        data = app_json(self.call([kind, "inspect", target], timeout=15))
        app_need(isinstance(data, list) and len(data) == 1); return data[0]

    def memory_states(self):
        self.check_cancel();states={};live_probes=0;pids=set()
        app_need(len(set(self.ids.values()))==len(self.ids))
        for role in self.ids:
            app_need(role in APP_ROLES+("namespace","api","session","media"))
            info=self.guard(role);v=info["State"]
            app_need(type(v.get("Pid")) is int and isinstance(v.get("StartedAt"),str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z",v["StartedAt"]))
            if v["Running"]:
                app_need(v["Status"]=="running" and 0<v["Pid"]<=2**31-1 and v["Pid"] not in pids and v["ExitCode"]==0 and not v["StartedAt"].startswith("0001-"))
                pids.add(v["Pid"]);live_probes+=role not in APP_ROLES
            else:app_need(v["Pid"]==0 and v["Status"] in ("created","exited"))
            states[role]=(v["Running"],v["Status"],v["Pid"],v["StartedAt"],v["ExitCode"])
        app_need(live_probes<=1);return states

    def memory_credit(self,role,state):
        app_need(state[0] is True)
        target=self.ids[role];app_need(re.fullmatch(r"[a-f0-9]{64}",target))
        membership="/system.slice/docker-"+target+".scope"
        uid,gid=map(int,self.specs[role]["user"].split(":"))
        cap=APP_LIMITS.get(role,APP_LIMITS["probe"])*1024**2
        cg=app_owned_dir(APP_CGROUP_ROOT/membership.lstrip("/"))
        try:
            identity=os.fstat(cg)
            def child_entries():
                children=os.listdir(cg);app_need(len(children)<=256 and len(children)==len(set(children)))
                entries={}
                for name in children:
                    st=os.stat(name,dir_fd=cg,follow_symlinks=False)
                    app_need(stat.S_ISREG(st.st_mode) and (st.st_uid,st.st_gid)==(0,0) and not st.st_mode & 0o022)
                    entries[name]=(st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid)
                app_need(set(os.listdir(cg))==set(children));return entries
            children_before=child_entries()
            def members():
                values=app_members(app_metadata(cg,"cgroup.procs"));app_need(state[2] in values)
                ids={p:app_process_identity(p,membership,uid,gid) for p in values}
                app_need(len({v[3:] for v in ids.values()})==1)
                return ids
            before=members()
            app_need(app_numeric(app_metadata(cg,"memory.max"))==cap)
            def swap():
                try:app_need(app_numeric(app_metadata(cg,"memory.swap.max"))==0);return True
                except FileNotFoundError:return False  # only this optional final file may be unsupported
            supported=swap()
            values=app_memory_stat(app_metadata(cg,"memory.stat"));credit=values["anon"]+values["shmem"]
            app_need(credit<=cap and members()==before and app_numeric(app_metadata(cg,"memory.max"))==cap and swap()==supported)
            check=app_owned_dir(APP_CGROUP_ROOT/membership.lstrip("/"))
            try:
                current=os.fstat(check);app_need((current.st_dev,current.st_ino)==(identity.st_dev,identity.st_ino))
            finally:os.close(check)
            # Hierarchical counters are attributable only while this same no-child
            # entry set stays strict/stable through the entire credit sample.
            app_need(child_entries()==children_before)
            return credit,before
        finally:os.close(cg)

    def capacity(self):
        # Credit only sampled resident owned memory already deducted from MemAvailable.
        # Whole limits, file cache, kernel and slab never reduce the unchanged peak/reserve.
        states=self.memory_states();fs=os.statvfs(self.plan["family"]["target"])
        available=[app_memory_available()];samples=[]
        for _ in range(2):
            sample={role:self.memory_credit(role,v) for role,v in states.items() if v[0]}
            app_need(self.memory_states()==states);samples.append(sample);available.append(app_memory_available())
        app_need(all(samples[0][r][1]==samples[1][r][1] for r in samples[0]))
        credit=sum(min(samples[0][r][0],samples[1][r][0]) for r in samples[0])
        final=self.memory_states();app_need(final==states);available.append(app_memory_available())
        end=os.statvfs(self.plan["family"]["target"])
        free=min(fs.f_bavail*fs.f_frsize,end.f_bavail*end.f_frsize)
        self.event({"state":"memory-admission","available_bytes":min(available),"owned_resident_credit_bytes":credit,
                    "effective_available_bytes":min(available)+credit,"required_bytes":APP_REQUIRED_MEMORY})
        app_admit(min(available)+credit,free,self.plan["family"]["write_growth_budget_bytes"])

    def family(self):
        target = Path(self.plan["family"]["target"]); root = app_open_dir(target)
        self.handles["root"] = root
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.identity["root"] = os.fstat(root).st_dev, os.fstat(root).st_ino
        app_need(os.fstat(root).st_uid == 0 and stat.S_IMODE(os.fstat(root).st_mode) == 0o700)
        app_need(set(os.listdir(root)) == set(self.plan["trees"]) | {"plan.json", "result.json", "app-read"})
        rawplan = app_read_file(target / "plan.json", self.plan["family"]["plan_sha256"])
        rawresult = app_read_file(target / "result.json", self.plan["family"]["result_sha256"])
        saved, result = app_json(rawplan), app_json(rawresult)
        app_need(saved["plan"]["target"] == str(target) and saved["plan_sha256"] == app_digest(saved["plan"]))
        app_need(saved["plan"]["evidence"]["current-backup-verified.json"] == APP_ACQUISITION
                 and saved["plan"]["executor_sha256"] == "bcd2e75dc5a79133cd439c5510b27d6c143e2f07e02654c61ac9491448bc33be"
                 and result["plan_sha256"] == saved["plan_sha256"] and result["storage_extraction_verified"] is True
                 and result["components"] == self.plan["trees"])
        for suffix, expected in self.plan["trees"].items():
            fd = app_open_dir(target / suffix); self.handles[suffix] = fd
            app_need(app_mount_id(fd) == app_mount_id(root))
            rows, measured = app_tree(fd); app_need(measured == expected and rows["."][3] == 0o700)
            app_need(not {"postmaster.pid", "standby.signal", "recovery.signal", ".arkx-engine-hba.conf"} & set(rows))
            st = os.fstat(fd); self.identity[suffix] = st.st_dev, st.st_ino
            if suffix == "postiz-uploads": self.media = {k: v[-1] for k,v in rows.items() if v[0] == "file"}
        cfg = target / "app-read"; fd = app_open_dir(cfg); self.handles["config"] = fd
        self.identity["config"] = os.fstat(fd).st_dev, os.fstat(fd).st_ino
        app_need(os.fstat(fd).st_uid == 0 and stat.S_IMODE(os.fstat(fd).st_mode) == 0o755
                 and set(os.listdir(fd)) == {"pg_hba.conf", "current.yaml", "counts.json"})
        app_need(app_read_file(cfg / "pg_hba.conf") == APP_HBA)
        count = app_json(app_read_file(cfg / "counts.json", self.plan["family"]["count_evidence_sha256"]))
        app_need(count["rows_exposed"] == 0 and count["counts"] == self.plan["count_baseline"]["counts"])
        for name in ("pg_hba.conf","current.yaml","counts.json"):
            meta=(cfg/name).lstat()
            app_need(stat.S_ISREG(meta.st_mode) and meta.st_uid==0 and meta.st_nlink==1 and stat.S_IMODE(meta.st_mode)==0o644)
        conf = app_json(app_read_file(cfg / "current.yaml", self.plan["family"]["config_sha256"]))
        self.temporal_config(conf)
        app_need(bool(self.media))
        marker = os.open(".app-read-used", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=root)
        try: os.write(marker, (app_digest(self.plan)+"\n").encode()); os.fsync(marker)
        finally: os.close(marker)
        os.fsync(root)

    def temporal_config(self, conf):
        # Operator supplies reviewed, JSON-shaped YAML from the exact image. No templating,
        # passwords, metrics exporters or external topology allowed. No default guessed config.
        app_need(isinstance(conf, dict) and set(conf) <= {"log", "persistence", "global", "services", "clusterMetadata", "dcRedirectionPolicy", "dynamicConfigClient"})
        stores = conf["persistence"]["datastores"]
        app_need(set(stores) == {"default", "visibility", "es-visibility"} and conf["persistence"]["defaultStore"] == "default"
                 and conf["persistence"]["visibilityStore"] == "es-visibility" and conf["persistence"]["numHistoryShards"] == 4)
        for name, database in (("default","temporal"),("visibility","temporal_visibility")):
            sql = stores[name]["sql"]
            app_need(sql["connectAddr"] == self.plan["names"]["pg16"] + ":5432" and sql["databaseName"] == database
                     and sql["pluginName"] == "postgres12" and sql["user"] == self.private["PG16_USER"] and not sql.get("password"))
        es = stores["es-visibility"]["elasticsearch"]
        app_need(es["version"] == "v7" and es["indices"] == {"visibility":"temporal_visibility_v1_dev"}
                 and es["url"] == {"scheme":"http","host":self.plan["names"]["es"]+":9200"})
        app_need(conf["dynamicConfigClient"]["filepath"] == APP_TEMPORAL_DYNAMIC)
        permitted = {self.plan["names"]["pg16"] + ":5432", self.plan["names"]["es"] + ":9200", "127.0.0.1:7233"}
        def walk(value, key=""):
            if isinstance(value, dict):
                for k, v in value.items():
                    app_need(isinstance(k, str) and not any(w in k.lower() for w in ("password", "token", "secret", "tls", "exporter", "archival", "replicat")))
                    walk(v, k)
            elif isinstance(value, list):
                for v in value: walk(v, key)
            elif isinstance(value, str):
                app_need(not any(c in value for c in ("$", "\n", "\r", "\\")) and len(value) <= 150)
                if ":" in value: app_need(value in permitted)
                if value.startswith("/"): app_need(value == APP_TEMPORAL_DYNAMIC)
                if key.lower() in {"bindonip", "broadcastaddress", "bindip"}: app_need(value in {"0.0.0.0", "127.0.0.1"})
            else: app_need(value is None or type(value) in (bool, int, float))
        walk(conf)

    def boundary(self):
        self.check_cancel(); mounts = app_mounts()
        rootpath = Path(self.plan["family"]["target"])
        st = rootpath.lstat(); app_need(stat.S_ISDIR(st.st_mode) and (st.st_dev,st.st_ino) == self.identity["root"])
        c=(rootpath/"app-read").lstat();app_need(stat.S_ISDIR(c.st_mode) and (c.st_dev,c.st_ino)==self.identity["config"])
        protected = [app_physical(rootpath, mounts)]
        for suffix, fd in self.handles.items():
            if suffix in self.plan["trees"]:
                st = os.stat(self.plan["family"]["target"] + "/" + suffix, follow_symlinks=False)
                app_need((st.st_dev, st.st_ino) == self.identity[suffix])
                protected.append(app_physical(Path(self.plan["family"]["target"]) / suffix, mounts))
        raw = self.call(["container", "ls", "-a", "--no-trunc", "--format", "{{.ID}}"], timeout=15)
        ids = raw.decode().splitlines(); app_need(len(ids) <= 256 and len(set(ids)) == len(ids))
        for target in ids:
            app_need(re.fullmatch(r"[a-f0-9]{64}", target))
            if target in self.ids.values():
                self.guard(next(r for r,v in self.ids.items() if v == target)); continue
            info = self.inspect("container", target)
            app_need(info["Name"].lstrip("/") not in self.plan["names"].values())
            for m in info["Mounts"]:
                if m["Type"] == "tmpfs": continue
                app_need(m["Type"] in ("bind", "volume"))
                path = app_absolute(m["Source"]).resolve(strict=True); st = path.stat()
                app_need(not stat.S_ISREG(st.st_mode) or st.st_nlink == 1)
                exposed = [app_physical(path, mounts)] + [(d,r) for d,r,p in mounts if path in p.parents]
                app_need(not any(d == pd and (not isinstance(r, Path) or app_overlap(r, pr)) for d,r in exposed for pd,pr in protected))
        if self.network_created: self.network_guard()

    def network_guard(self):
        n = self.inspect("network", self.plan["network"])
        app_need(n["Id"] == self.network_id and n["Internal"] is True and n["Driver"] == "bridge" and n["Labels"] == self.labels
                 and set(n.get("Containers") or {}) <= set(self.ids.values()) and not n.get("Attachable"))

    def spec(self, role, program=None):
        base = {"user": "65534:65534", "entry": "node", "args": ["-e", program or ""], "mounts": [], "env": {}, "cwd": "/app"}
        target = self.plan["family"]["target"]; names = self.plan["names"]
        if role in APP_DATA:
            suffix, dest = APP_DATA[role]; st = os.fstat(self.handles[suffix]); base["user"] = str(st.st_uid)+":"+str(st.st_gid)
            app_need(st.st_uid > 0)
            base["mounts"] = [(target+"/"+suffix, dest, role not in ("redis",))]
            if role in ("pg14", "pg16"):
                base.update(entry="postgres", args=["-D",dest,"-c","hba_file=/app-read/pg_hba.conf","-c","listen_addresses=*",
                  "-c","unix_socket_directories=/tmp","-c","ssl=off","-c","logging_collector=off","-c","archive_mode=off",
                  "-c","archive_command=","-c","restore_command=","-c","primary_conninfo=","-c","shared_preload_libraries=",
                  "-c","session_preload_libraries=","-c","local_preload_libraries=","-c","external_pid_file=",
                  "-c","shared_buffers=16MB","-c","max_connections=30","-c","statement_timeout=5000","-c","lock_timeout=5000",
                  "-c","default_transaction_read_only="+("on" if role=="pg14" else "off")])
                base["mounts"].append((target+"/app-read", "/app-read", False))
            elif role == "redis": base.update(entry="redis-server", args=["--dir","/data","--dbfilename","dump.rdb","--bind","0.0.0.0","--protected-mode","no","--save","","--appendonly","no"])
            else:
                base.update(entry="/bin/bash", cwd="/usr/share/elasticsearch", args=["-c",APP_ES_STARTUP,"app-read-es","-Epath.data="+dest,"-Epath.logs=/usr/share/elasticsearch/logs","-Enetwork.host=0.0.0.0","-Ediscovery.type=single-node","-Expack.security.enabled=false","-Expack.ml.enabled=false"], env={"ES_JAVA_OPTS":APP_ES_JAVA_OPTS})
        elif role == "media":
            base["user"] = "0:0"  # read-only uploads retain their acquired 0700 ownership
            base["mounts"] = [(target+"/postiz-uploads", "/uploads", False)]
        elif role == "temporal":
            base.update(entry=self.plan["family"]["temporal_entrypoint"],args=["--config-dir","/app-read","--env","current","start"],mounts=[(target+"/app-read","/app-read",False)])
        elif role == "backend":
            from urllib.parse import quote
            p = self.private
            base.update(cwd="/app/apps/backend", args=["--experimental-require-module","./dist/apps/backend/src/main.js"], mounts=[(target+"/postiz-uploads","/uploads",False)], env={
                "NODE_ENV":"production", "JWT_SECRET":p["JWT_SECRET"], "DATABASE_URL":"postgresql://"+quote(p["PG14_USER"],safe="")+"@"+names["pg14"]+":5432/"+quote(p["PG14_DB"],safe=""),
                "REDIS_URL":"redis://"+names["redis"]+":6379", "TEMPORAL_ADDRESS":names["temporal"]+":7233", "TEMPORAL_NAMESPACE":p["TEMPORAL_NAMESPACE"],
                "IS_GENERAL":"true", "DISABLE_REGISTRATION":"true", "STORAGE_PROVIDER":"local", "UPLOAD_DIRECTORY":"/uploads", "PORT":"3000",
                "MAIN_URL":"http://"+names["backend"]+":3000", "FRONTEND_URL":"http://"+names["backend"]+":3000", "NX_ADD_PLUGINS":"false"})
        return base

    def temporary(self, role):
        if role == "es":
            return {"/tmp":"rw,exec,nosuid,nodev,size=130023424,mode=1777", "/usr/share/elasticsearch/logs":"rw,noexec,nosuid,nodev,size=4194304,uid=1000,gid=0,mode=0700"}
        return {"/tmp":"rw,noexec,nosuid,nodev,size=67108864,mode=1777"}

    def create(self, role, program=None, env=None):
        self.boundary(); self.capacity(); self.check_cancel()
        spec = self.spec(role, program); spec["env"].update(env or {}); self.specs[role] = spec
        limit = APP_LIMITS.get(role, APP_LIMITS["probe"]) * 1024**2
        image = self.plan["images"].get(role, APP_IMAGE)
        opts = ["container","create","--name",self.plan["names"][role],"--pull=never","--network",self.plan["network"],"--restart=no","--no-healthcheck","--log-driver=none","--cap-drop=ALL","--security-opt=no-new-privileges","--read-only","--user",spec["user"],"--workdir",spec["cwd"],"--memory",str(limit),"--memory-swap",str(limit),"--pids-limit","128","--cpus","0.5","--entrypoint",spec["entry"],"--interactive"]
        for point, flags in self.temporary(role).items(): opts += ["--tmpfs",point+":"+flags]
        for k,v in self.labels.items(): opts += ["--label",k+"="+v]
        for src,dst,rw in spec["mounts"]: opts += ["--mount","type=bind,src="+src+",dst="+dst+("" if rw else ",readonly")]
        app_need(all(isinstance(v,str) and not any(c in v for c in "\n\r\0") for v in spec["env"].values()))
        payload = None
        if spec["env"]:
            opts += ["--env-file","/dev/stdin"]; payload = "".join(k+"="+v+"\n" for k,v in spec["env"].items()).encode()
        self.attempted.append(role); self.event({"role":role,"state":"create-attempt"})
        target = self.call(opts+[image]+spec["args"],payload=payload,timeout=30).decode().strip()
        app_need(re.fullmatch(r"[a-f0-9]{64}",target)); self.ids[role] = target
        self.event({"role":role,"id":target,"state":"created"}); self.guard(role)
        return target

    def guard(self, role):
        v = self.inspect("container",self.ids[role]); s = self.specs[role]; h = v["HostConfig"]; c = v["Config"]
        limit = APP_LIMITS.get(role, APP_LIMITS["probe"]) * 1024**2
        app_need(v["Id"] == self.ids[role] and v["Name"] == "/"+self.plan["names"][role] and v["Image"] == self.plan["images"].get(role,APP_IMAGE)
          and c.get("Labels") == {**self.image_labels[self.plan["images"].get(role,APP_IMAGE)],**self.labels} and c["Entrypoint"] == [s["entry"]] and c["Cmd"] == s["args"] and c["User"] == s["user"] and c["WorkingDir"] == s["cwd"]
          and c["Healthcheck"]["Test"] == ["NONE"] and h["NetworkMode"] == self.plan["network"] and set(v["NetworkSettings"]["Networks"]) == {self.plan["network"]}
          and not h.get("PortBindings") and not h.get("ExtraHosts") and not h["Privileged"] and not h["AutoRemove"] and h["ReadonlyRootfs"]
          and h["CapDrop"] == ["ALL"] and h["SecurityOpt"] in (["no-new-privileges"],["no-new-privileges:true"])
          and h["RestartPolicy"] == {"Name":"no","MaximumRetryCount":0} and h["LogConfig"]["Type"] == "none"
          and h["PidMode"] == "" and h["IpcMode"] == "private" and h["Memory"] == h["MemorySwap"] == limit and h["NanoCpus"] == 500000000 and h["PidsLimit"] == 128
          and h["Tmpfs"] == self.temporary(role))
        temporary = [m for m in v["Mounts"] if m["Type"] == "tmpfs"]
        app_need(len(temporary) <= len(self.temporary(role)) and all(m["Destination"] in self.temporary(role) for m in temporary))
        image_env = self.image_env[self.plan["images"].get(role,APP_IMAGE)]
        expected_env = dict(image_env); expected_env.update(s["env"])
        app_need(c["Env"] == [k+"="+val for k,val in expected_env.items()] or sorted(c["Env"]) == sorted(k+"="+val for k,val in expected_env.items()))
        mounts = [m for m in v["Mounts"] if m["Type"] != "tmpfs"]
        app_need(len(mounts) == len(s["mounts"]) and sorted((m["Type"],m["Source"],m["Destination"],m["RW"]) for m in mounts) == sorted(("bind",a,b,rw) for a,b,rw in s["mounts"]))
        app_need(type(v["State"]["Running"]) is bool and type(v["State"]["OOMKilled"]) is bool and type(v["State"]["ExitCode"]) is int and not v["State"]["OOMKilled"])
        return v

    def start(self, role, payload=None, attached=False):
        self.boundary(); self.capacity(); self.guard(role); self.check_cancel()
        if role not in APP_ROLES:
            app_need(not any(v[0] for r,v in self.memory_states().items() if r not in APP_ROLES))
        if role in APP_DATA:
            suffix=APP_DATA[role][0]
            app_need(app_tree(self.handles[suffix])[1] == self.plan["trees"][suffix])
        cfg=Path(self.plan["family"]["target"])/"app-read"
        app_need(app_read_file(cfg/"pg_hba.conf") == APP_HBA)
        app_read_file(cfg/"current.yaml",self.plan["family"]["config_sha256"])
        self.event({"role":role,"state":"start-attempt"})
        out = self.call(["container","start"]+(["--attach","--interactive"] if attached else [])+[self.ids[role]],payload=payload,timeout=90)
        self.guard(role); self.started.add(role)
        if attached:
            app_need(not self.guard(role)["State"]["Running"] and self.guard(role)["State"]["ExitCode"] == 0)
        return out

    def ready(self, role):
        # A timeout reading state can be retried; never retry create/start/stop.
        if role == "temporal": return  # namespace probe below is its bounded readiness/read check
        if role in ("pg14","pg16"): argv=["pg_isready","-h","127.0.0.1","-p","5432"]
        elif role == "redis": argv=["redis-cli","ping"]
        elif role == "es": argv=["curl","-fsS","--max-time","2","http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=1s"]
        else: argv=["node","-e","fetch('http://127.0.0.1:3000/auth/can-register',{signal:AbortSignal.timeout(1000)}).then(r=>{if(r.status===200)process.stdout.write('APP_READY');else process.exitCode=1}).catch(()=>process.exitCode=1)"]
        started=time.monotonic();budget=120 if role=="es" else 30;deadline=started+budget
        while time.monotonic()<deadline:
            self.check_cancel(); self.guard(role)
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            out=self.call(["container","exec",self.ids[role]]+argv,timeout=min(4,remaining),allow_failure=True)
            now=time.monotonic()
            if now>=deadline:break
            if role in ("pg14","pg16") and b"accepting connections" in out: return
            if role == "redis" and out.strip()==b"PONG": return
            if role == "es" and out:
                v=app_json(out)
                app_need(isinstance(v,dict) and v.get("status") in ("green","yellow","red") and type(v.get("timed_out")) is bool)
                if v.get("status") in ("green","yellow") and v.get("timed_out") is False:
                    elapsed=now-started;app_need(0<=elapsed<budget)
                    self.event({"state":"es-ready","elapsed_ms":int(elapsed*1000)});return
            if role == "backend" and out==b"APP_READY": return
            time.sleep(min(.25,max(0,deadline-time.monotonic())))
        raise Refusal("App-read readiness failed; exact resources retained")

    def process_guard(self, role):
        self.guard(role)
        raw = self.call(["container","top",self.ids[role],"-eo","args"],timeout=10).decode().splitlines()
        app_need(1 < len(raw) <= 40)
        allowed = {"pg14":{"postgres"},"pg16":{"postgres"},"redis":{"redis-server"},"es":{"java","controller"},"temporal":{"temporal-server"},"backend":{"node"}}[role]
        for row in raw[1:]:
            app_need(row.strip() and Path(row.split()[0]).name.split(":")[0] in allowed)
            app_need(not any(x in row for x in ("orchestrator","pm2","auto-setup","prisma","workflow-worker")))

    def counts(self):
        p = self.private
        selects = ['SELECT count(*) FROM public.'+quote_ident(t)+';' for t in APP_COUNTS[:6]]
        selects += ['SELECT count(*) FROM public."Post" WHERE "state"=\'QUEUE\';', 'SELECT count(*) FROM public."Post" WHERE "state"=\'PUBLISHED\';', 'SELECT count(*) FROM public."Organization" WHERE "apiKey" IS NULL OR "apiKey"=\'\';']
        args = ["container","exec",self.ids["pg14"],"psql","--no-password","-X","-qAt","-v","ON_ERROR_STOP=1","-U",p["PG14_USER"],"-d",p["PG14_DB"],"-c","BEGIN READ ONLY;"]
        for q in selects: args += ["-c",q]
        args += ["-c","ROLLBACK;"]
        self.guard("pg14"); result = self.call(args,timeout=20).decode().splitlines()
        app_need(len(result) == len(APP_COUNTS) and all(re.fullmatch(r"[0-9]+",r) for r in result))
        actual = dict(zip(APP_COUNTS,map(int,result))); app_need(all(v is None or actual[k] == v for k,v in self.plan["family"]["counts"].items()))
        return actual

    def probe(self, role, source, private=None, env=None):
        self.create(role,source,env); result = app_json(self.start(role,app_canonical(private or {}),True))
        return result

    def shutdown(self):
        # No deletion or forced escalation; uncertainty is retained, never retried.
        for role in reversed(list(self.ids)):
            if role in self.stopped: continue
            try:
                term_confirmed = False
                v = self.guard(role)
                if v["State"]["Running"]:
                    self.event({"role":role,"state":"term-attempt"})
                    self.call(["container","kill","--signal=SIGTERM",self.ids[role]],timeout=10)
                    term_confirmed = True
                    deadline = time.monotonic()+35
                    while self.guard(role)["State"]["Running"] and time.monotonic() < deadline: time.sleep(.2)
                v = self.guard(role)
                allowed = {0,143} if term_confirmed and role not in ("pg14","pg16","redis") else {0}
                app_need(not v["State"]["Running"] and v["State"]["Status"] in ("exited","created") and v["State"]["ExitCode"] in allowed)
                self.stopped[role] = True; self.event({"role":role,"state":"stopped"})
            except (Refusal,OSError,ValueError,KeyError) as error:
                self.record_refusal(error,"shutdown");self.stopped[role] = False

    def execute(self):
        app_need(set(self.private) <= {"PG14_USER","PG14_DB","PG16_USER","JWT_SECRET","API_KEY","SESSION_COOKIE","TEMPORAL_NAMESPACE"}
          and {"PG14_USER","PG14_DB","PG16_USER","JWT_SECRET","API_KEY","TEMPORAL_NAMESPACE"} <= set(self.private))
        app_need(all(isinstance(v,str) and 0 < len(v) <= 16384 and not any(c in v for c in "\r\n\0") for v in self.private.values()))
        for k in ("PG14_USER","PG14_DB","PG16_USER"): app_need(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}",self.private[k]))
        if "SESSION_COOKIE" in self.private: app_need(re.fullmatch(r"auth=[A-Za-z0-9_.%~-]+",self.private["SESSION_COOKIE"]))
        self.network_created = False; self.image_env = {}; self.image_labels = {}
        _,_,checks,_ = app_inputs()
        self.family(); self.capacity(); self.boundary()
        for image in set(self.plan["images"].values()):
            info = self.inspect("image",image); app_need(info["Id"] == image and info["Architecture"] == "amd64" and info["Os"] == "linux")
            labels=info["Config"].get("Labels")  # Docker omits this optional field for empty baked labels.
            app_need(labels is None or isinstance(labels,dict))
            labels={} if labels is None else labels
            app_need(all(isinstance(k,str) and isinstance(v,str) for k,v in labels.items()) and not set(labels).intersection(self.labels))
            self.image_labels[image]=dict(labels)
            env = dict(x.split("=",1) for x in info["Config"].get("Env") or [])
            app_need(set(env) <= SAFE_IMAGE_ENV | {"JAVA_HOME","ES_HOME","ES_PATH_CONF","TINI_SUBREAPER","TEMPORAL_HOME"} | APP_STOCK_IMAGE_ENV.get(image,frozenset()))
            app_need(len(env)==len(info["Config"].get("Env") or []))
            for role in APP_ROLES+("namespace","api","session","media"):
                if self.plan["images"].get(role,APP_IMAGE)==image:
                    app_need(set(info["Config"].get("Volumes") or {}) <= {dst for _,dst,_ in self.spec(role)["mounts"]})
            self.image_env[image] = env
        names = self.call(["container","ls","-a","--format","{{.Names}}"],timeout=15).decode().splitlines()
        app_need(not set(names) & set(self.plan["names"].values()))
        app_need(self.plan["network"] not in self.call(["network","ls","--format","{{.Name}}"],timeout=15).decode().splitlines())
        opts=["network","create","--driver","bridge","--internal"]
        for k,v in self.labels.items(): opts += ["--label",k+"="+v]
        self.network_id = self.call(opts+[self.plan["network"]],timeout=20).decode().strip(); self.network_created=True; self.network_guard()
        try:
            for role in APP_ROLES[:5]:
                self.create(role); self.start(role); self.ready(role)
            # Namespace must be readable BEFORE backend bootstrap can register attributes.
            nsenv={"TEMPORAL_ADDRESS":self.plan["names"]["temporal"]+":7233","TEMPORAL_NAMESPACE":self.private["TEMPORAL_NAMESPACE"]}
            namespace=self.probe("namespace",checks["check-temporal.cjs"]+"\nmain([]).catch(()=>{process.stdout.write('{\"ok\":false}');process.exitCode=1});",env=nsenv)
            app_need(namespace == {"namespaceState":"REGISTERED","textAttributes":{"organizationId":True,"postId":True}})
            before=self.counts()
            self.create("backend"); self.start("backend"); self.ready("backend")
            for role in APP_ROLES: self.process_guard(role)
            api=self.probe("api",checks["check-backend.cjs"]+"\nmain(['--allow-host',process.env.APP_HOST]).catch(()=>{process.stdout.write('{\"ok\":false}');process.exitCode=1});",{"API_KEY":self.private["API_KEY"]},{"BASE_URL":"http://"+self.plan["names"]["backend"]+":3000","EXPECTED_INTEGRATIONS":str(self.plan["family"]["api_integrations"]),"APP_HOST":self.plan["names"]["backend"]})
            app_need(set(api["checks"]) == {"apiKeyValid","integrationCount","missingKeyRejected","invalidKeyRejected","registrationClosed"} and all(v is True for v in api["checks"].values()))
            session=False
            if "SESSION_COOKIE" in self.private:
                js="""(async()=>{let s='';for await(const c of process.stdin)s+=c;const p=JSON.parse(s);const r=await fetch('http://'+process.env.APP_HOST+':3000/user/self',{headers:{Cookie:p.cookie},redirect:'error',signal:AbortSignal.timeout(10000)});const v=r.ok?await r.json():null;process.stdout.write(JSON.stringify({ok:r.status===200&&!!v&&typeof v.id==='string'}));process.exitCode=r.status===200&&!!v&&typeof v.id==='string'?0:1})().catch(()=>{process.stdout.write('{"ok":false}');process.exitCode=1});"""
                session=self.probe("session",js,{"cookie":self.private["SESSION_COOKIE"]},{"APP_HOST":self.plan["names"]["backend"]}) == {"ok":True};app_need(session)
            js="""(async()=>{let s='';for await(const c of process.stdin)s+=c;const files=JSON.parse(s);const fs=require('node:fs'),crypto=require('node:crypto');let n=0;for(const [p,h]of Object.entries(files)){if(p.split('/').includes('..')||p.startsWith('/'))throw Error();const b=fs.readFileSync('/uploads/'+p);if(crypto.createHash('sha256').update(b).digest('hex')!==h)throw Error();n++}process.stdout.write(JSON.stringify({matched:true,files:n}))})().catch(()=>{process.stdout.write('{"matched":false}');process.exitCode=1});"""
            # Media probe gets ONLY read-only restored uploads, no provider or session settings.
            self.specs.pop("media",None)
            media=self.probe("media",js,self.media)
            app_need(media == {"matched":True,"files":len(self.media)})
            app_need(self.counts() == before)
            self.passed={"accounts":True,"api":True,"session":session,"mediaBytes":True,"namespace":True,"fullApplicationRecovery":False,"fullWorkflowRecovery":False}
        finally:
            self.shutdown()
        app_need(all(self.stopped.values()) and len(self.stopped)==len(self.ids))
        self.network_guard()
        return {"checks":self.passed,"counts":before,"session_gap":not session,"retained":True,"stopped":len(self.ids),"plan_sha256":app_digest(self.plan)}


def app_read_main(args, input_stream=None):
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    input_stream = input_stream or sys.stdin.buffer
    raw = input_stream.read(65537); app_need(len(raw)<=65536)
    supplied = app_json(raw) if raw.strip() else {}
    app_need(isinstance(supplied,dict) and set(supplied) <= {"family","private"})
    plan=app_plan(args,supplied.get("family")); fingerprint=app_digest(plan)
    if not args.app_read_execute:
        app_need(not args.confirm_app_read and "private" not in supplied); emit(json.dumps({"plan":plan,"plan_sha256":fingerprint},sort_keys=True)); return
    app_need(plan["ready"] and args.confirm_app_read == fingerprint and sys.platform=="linux" and os.geteuid()==0)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    private=supplied.get("private");app_need(isinstance(private,dict))
    runner=AppRead(plan,private)
    def cancel(signum,frame): runner.cancelled=True
    previous={sig:signal.signal(sig,cancel) for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    lock=None
    try:
        # Exclusive private journal is outside all extraction families; never remove/reuse it.
        base=Path('/var/lib/arkx-current-app-read-runs'); fd=app_open_dir(base)
        try:
            app_need(os.fstat(fd).st_uid==0 and stat.S_IMODE(os.fstat(fd).st_mode)==0o700)
            os.mkdir(args.prefix,0o700,dir_fd=fd)
            child=os.open(args.prefix,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try: runner.journal=os.open('journal.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=child)
            finally:os.close(child)
        finally:os.close(fd)
        result=runner.checked_execute();emit(json.dumps(result,sort_keys=True))
    finally:
        if runner.ids: runner.shutdown()
        for fd in runner.handles.values():os.close(fd)
        if runner.journal is not None:os.close(runner.journal)
        for sig,handler in previous.items():signal.signal(sig,handler)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--stage", choices=("plan", "db-schema", "cleanup", "app-read"), default="plan")
    result.add_argument("--prefix", required=True)
    result.add_argument("--run-id", required=True)
    result.add_argument("--pg-image")
    result.add_argument("--candidate-image")
    result.add_argument("--backup-dir")
    result.add_argument("--dump-relative")
    result.add_argument("--dump-sha256")
    result.add_argument("--probe-host", default="example.com")
    result.add_argument("--confirm-cleanup")
    result.add_argument("--app-read-execute", action="store_true")
    result.add_argument("--confirm-app-read")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.stage == "app-read":
        try:
            return app_read_main(args)
        except TypeError:
            raise Refusal("App-read malformed input; details withheld") from None
    need(not args.app_read_execute and not args.confirm_app_read, "App-read flags require app-read stage")
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
