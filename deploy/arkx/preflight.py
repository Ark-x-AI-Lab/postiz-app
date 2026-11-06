#!/usr/bin/env python3
"""Read-only, stdlib-only candidate checks. Never starts/builds containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?[^}]+\}")
APP_IMAGE_PREFIX = "ghcr.io/ark-x-ai-lab/postiz-app:sha-"
IMAGES = {
    "postiz-postgres": "postgres:14.5",
    "postiz-redis": "redis:7.2",
    "temporal-postgresql": "postgres:16",
    "temporal-elasticsearch": "elasticsearch:7.17.27",
    "temporal": "temporalio/auto-setup:1.28.1",
    "temporal-admin-tools": "temporalio/admin-tools:1.28.1-tctl-1.18.4-cli-1.4.1",
    "temporal-ui": "temporalio/ui:2.34.0",
}
OAUTH = {
    "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def load(mode):
    return json.loads((HERE / f"compose.{mode}.yaml").read_text())


def names(mode):
    return set(REQUIRED.findall(json.dumps(load(mode))))


def pinned_app_image(source_commit):
    need(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None,
         "SOURCE_COMMIT must be a full 40-character lowercase hexadecimal commit SHA")
    return APP_IMAGE_PREFIX + source_commit



def validate_public_pnpm_config(text):
    """Only reviewed public settings may enter the exported dependency cache."""
    need(len(text.encode("utf-8")) <= 8192, "Package-manager configuration is too large; values withheld")
    allowed = {
        "ignore-workspace-root-check": {"true", "false"},
        "node-linker": {"hoisted"},
        "restrict-manifest-changes": {"true", "false"},
        "sync-injected-deps-after-scripts[]": {"build"},
        "inject-workspace-packages": {"true"},
    }
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        need(separator and key in allowed and key not in seen,
             "Unreviewed package-manager configuration; values withheld")
        need(value in allowed[key], "Unreviewed package-manager setting value; values withheld")
        seen.add(key)
    need(seen == set(allowed), "Required public package-manager settings missing; values withheld")


def public_build_input_checks():
    config = ROOT / ".npmrc"
    need(not config.is_symlink() and config.is_file(), "Package-manager configuration must be a regular file")
    need(config.stat().st_size <= 8192, "Package-manager configuration is too large; values withheld")
    validate_public_pnpm_config(config.read_text(encoding="utf-8"))
    print("PASS: five reviewed public package-manager settings; no unreviewed cache configuration")


def static_checks():
    for mode in ("rehearsal", "production"):
        data = load(mode)
        services = data["services"]
        need(set(services) == set(IMAGES) | ({"postiz"} if mode == "production" else set()),
             f"Unexpected {mode} service set")
        for name, service in services.items():
            need(not any(k in service for k in ("ports", "container_name", "network_mode", "env_file", "privileged")),
                 f"Unsafe service setting: {mode}/{name}")
            if name in IMAGES:
                need(service["image"] == IMAGES[name], f"Upstream image drift: {name}")
            for mount in service.get("volumes", []):
                source = mount.split(":")[0]
                need(source in data["volumes"] or (mode == "rehearsal" and name == "temporal"
                     and mount == "./deploy/arkx/dynamicconfig:/etc/temporal/config/dynamicconfig:ro"),
                     f"Unexpected mount: {mode}/{name}")
        if mode == "production":
            temporal = services["temporal"]
            need(temporal["environment"]["DYNAMIC_CONFIG_FILE_PATH"] == "config/dynamicconfig/docker.yaml"
                 and not temporal.get("volumes"), "Production Temporal must use unmounted image defaults")
        for key, volume in data["volumes"].items():
            expected = "arkx-postiz-${ARKX_CANDIDATE_ID:?Set ARKX_CANDIDATE_ID in the runtime environment}-" + mode + "-" + key
            need(volume == {"name": expected}, f"Unsafe volume definition: {mode}/{key}")
        for network, config in data["networks"].items():
            need(not config.get("external") and "name" not in config, "Network must be project-scoped")
            need(config.get("internal") is True or (mode == "production" and network == "app-egress"),
                 "Unexpected infrastructure egress")
        need(services["temporal"]["environment"]["ENABLE_ES"] == "true", "Keep Elasticsearch visibility")
        need(services["temporal-elasticsearch"]["volumes"] == ["temporal-elasticsearch:/usr/share/elasticsearch/data"],
             "Elasticsearch data must use its persistent image data directory")
    app = load("production")["services"]["postiz"]
    need(app["profiles"] == ["production"], "Publisher profile guard missing")
    need(app["expose"] == ["5000"], "App internal port must be 5000")
    need(app["command"] == ["sh", "/app/deploy/arkx/runtime-start.sh"], "Startup guard missing")
    need("build" not in app, "Deployment must pull the pinned application image, never build it")
    need(re.fullmatch(re.escape(APP_IMAGE_PREFIX) + r"\$\{SOURCE_COMMIT:\?[^}]+\}", app.get("image", "")) is not None,
         "Application image must use the fixed GHCR namespace and required SOURCE_COMMIT tag")
    need(app["environment"]["DISABLE_REGISTRATION"] == "true", "Registration must be disabled")
    need(OAUTH <= set(app["environment"]), "Selected provider settings missing")
    for key in OAUTH | {"JWT_SECRET", "ARKX_RUNTIME"}:
        need(REQUIRED.fullmatch(app["environment"][key]) is not None, f"Required runtime interpolation missing: {key}")
    need((HERE / "dynamicconfig/development-sql.yaml").read_bytes() == (ROOT / "dynamicconfig/development-sql.yaml").read_bytes(),
         "Temporal dynamic configuration differs from upstream")
    print("PASS: 7 infrastructure services (app PostgreSQL 14.5 retained), 0 published ports, isolated mode-specific volume names, runtime-only provider settings")


def guard_checks():
    with tempfile.TemporaryDirectory(prefix="arkx-postiz-guard-") as tmp:
        folder = Path(tmp)
        marker = folder / "calls"
        for name in ("nginx", "pnpm", "pm2-runtime"):
            stub = folder / name
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + ' $*" >> "$ARKX_TEST_MARKER"\n'
                            + f'if [ "${{ARKX_TEST_FAIL-}}" = "{name}" ]; then exit 73; fi\n'
                            + ('test "$$" = "$ARKX_TEST_WRAPPER_PID"\n' if name == "pm2-runtime" else ''))
            stub.chmod(0o700)
        base = {"PATH": f"{folder}:/usr/bin:/bin", "ARKX_TEST_MARKER": str(marker)}
        for mode in (None, "rehearsal", "Production", ""):
            env = dict(base)
            if mode is not None:
                env["ARKX_RUNTIME"] = mode
            result = subprocess.run(["sh", str(HERE / "runtime-start.sh")], env=env, capture_output=True)
            need(result.returncode == 64 and not marker.exists(), "Blocked runtime invoked a publisher command")
        invocation = ["sh", "-c", 'export ARKX_TEST_WRAPPER_PID=$$; exec sh "$1"',
                      "guard-check", str(HERE / "runtime-start.sh")]
        calls = ["nginx ", "pnpm run prisma-db-push",
                 "pm2-runtime start /app/deploy/arkx/ecosystem.config.cjs"]
        for fail, expected in (("nginx", calls[:1]), ("pnpm", calls[:2]), ("", calls)):
            if marker.exists():
                marker.unlink()
            result = subprocess.run(invocation, env={**base, "ARKX_RUNTIME": "production", "ARKX_TEST_FAIL": fail},
                                    capture_output=True)
            need(result.returncode == (73 if fail else 0) and marker.read_text().splitlines() == expected,
                 "Startup order, failure propagation or PM2 exec ownership changed")
    print("PASS: startup guard blocks 4 invalid/missing selections; nginx/schema failures stop startup; allowed runtime execs PM2 after guarded schema preparation")


def ecosystem_checks():
    result = subprocess.run(["node", "-e", "console.log(JSON.stringify(require(process.argv[1])))",
                             str(HERE / "ecosystem.config.cjs")], capture_output=True, text=True)
    need(result.returncode == 0, "Cannot load production PM2 configuration")
    apps = json.loads(result.stdout)["apps"]
    need(len(apps) == 3 and {app["name"] for app in apps} == {"backend", "orchestrator", "frontend"},
         "Production PM2 must supervise exactly three services")
    for app in apps:
        name = app["name"]
        need(app["cwd"] == f"/app/apps/{name}" and app["interpreter"] == "node"
             and app["exec_mode"] == "fork" and app["instances"] == 1
             and app["env"] == {"NODE_ENV": "production"}, "PM2 process ownership or production environment changed")
        package = json.loads((ROOT / f"apps/{name}/package.json").read_text())
        if name == "frontend":
            need(app["script"] == "/app/node_modules/next/dist/bin/next"
                 and app["args"] == ["start", "-p", "4200"]
                 and package["scripts"]["start"] == "dotenv -e ../../.env -- next start -p 4200",
                 "Frontend production command differs from upstream")
        else:
            script = f"./dist/apps/{name}/src/main.js"
            need(app["script"] == script and app["node_args"] == ["--experimental-require-module"]
                 and package["scripts"]["start"] == f"dotenv -e ../../.env -- node --experimental-require-module {script}",
                 "Service production command differs from upstream")
    package = json.loads((ROOT / "package.json").read_text())
    need(package["scripts"]["prisma-db-push"] == "pnpm exec prisma db push --schema ./libraries/nestjs-libraries/src/database/prisma/schema.prisma",
         "Installed Prisma schema guard changed")
    print("PASS: PM2 directly supervises all 3 Node services; commands match installed schema and upstream service paths without dotenv wrappers")


def compose(mode, env, *options):
    return subprocess.run(["docker", "compose", "--project-directory", str(ROOT),
                           "--env-file", os.devnull, "-f", str(HERE / f"compose.{mode}.yaml"),
                           *options], env=env, capture_output=True, text=True)


def compose_path_checks(env):
    # Reproduce Coolify's generated per-service env_file without real checkout secrets.
    with tempfile.TemporaryDirectory(prefix="arkx-postiz-paths-") as tmp:
        root = Path(tmp).resolve()
        nested = root / "deploy/arkx"
        dynamic = nested / "dynamicconfig"
        dynamic.mkdir(parents=True)
        shutil.copyfile(HERE / "dynamicconfig/development-sql.yaml", dynamic / "development-sql.yaml")
        (root / ".env").write_text("ARKX_PATH_PROBE=synthetic-validation-only\n")
        for mode in ("rehearsal", "production"):
            data = load(mode)
            for service in data["services"].values():
                service["env_file"] = [".env"]
            path = nested / f"compose.{mode}.yaml"
            path.write_text(json.dumps(data))

            def render(project):
                return subprocess.run(
                    ["docker", "compose", "-f", str(path), "--env-file", str(root / ".env"),
                     "--project-directory", str(project), "--profile", "production",
                     "config", "--format", "json"],
                    cwd=root, env=env, capture_output=True, text=True)

            # Compose versions differ in whether config retains env_file metadata.
            # Prove loading through every service's effective environment instead.
            result = render(root)
            need(result.returncode == 0, f"Root {mode} generated env_file resolution failed")
            services = json.loads(result.stdout)["services"]
            need(set(services) == set(data["services"]), f"Root {mode} fixture service set changed")
            for name, service in services.items():
                need(service.get("environment", {}).get("ARKX_PATH_PROBE") == "synthetic-validation-only",
                     f"Root {mode}/{name} generated env_file was not loaded")
            if mode == "rehearsal":
                mount = next(v for v in services["temporal"]["volumes"]
                             if v["target"] == "/etc/temporal/config/dynamicconfig")
                need(mount["type"] == "bind" and mount["source"] == str(dynamic)
                     and mount["read_only"] is True, "Root rehearsal dynamic configuration bind is wrong")
            else:
                need(not services["temporal"].get("volumes")
                     and services["temporal"]["environment"]["DYNAMIC_CONFIG_FILE_PATH"] == "config/dynamicconfig/docker.yaml",
                     "Root production Temporal must not depend on a host configuration path")
            if mode == "production":
                app = services["postiz"]
                need("build" not in app and app["image"] == pinned_app_image(env["SOURCE_COMMIT"]),
                     "Root application image is not the pinned source commit")
            result = render(nested)
            need(result.returncode != 0 and str(nested / ".env") in result.stderr
                 and "no such file" in result.stderr.lower(),
                 f"Nested {mode} must reject missing generated env_file")
    print("PASS: both Coolify-generated env_file fixtures load all 15 service markers from repository root; production uses unmounted Temporal image defaults; rehearsal bind matches; nested project directory fails on missing .env")


def compose_checks():
    if not shutil.which("docker"):
        print("GAP: Docker Compose CLI unavailable; rendering/profile checks not run")
        return
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "COMPOSE_PROFILES": ""}
    for key in names("production"):
        env[key] = "synthetic-validation-only"
    env.update(ARKX_CANDIDATE_ID="validation-only", ARKX_RUNTIME="production", SOURCE_COMMIT="0" * 40)
    result = subprocess.run(["docker", "compose", "version", "--short"],
                            env=env, capture_output=True, text=True)
    need(result.returncode == 0, "Docker Compose version lookup failed")
    print(f"Docker Compose version: {result.stdout.strip()}")
    for mode in ("rehearsal", "production"):
        result = compose(mode, env, "config", "--format", "json")
        need(result.returncode == 0, f"Compose {mode} rendering failed (output withheld)")
        services = json.loads(result.stdout)["services"]
        need("postiz" not in services, f"Default {mode} starts publisher")
        result = compose(mode, env, "--profile", "production", "config", "--format", "json")
        need(result.returncode == 0, f"Compose {mode} profile rendering failed")
        services = json.loads(result.stdout)["services"]
        need(("postiz" in services) == (mode == "production"), "Publisher profile isolation failed")
        if mode == "production":
            app = services["postiz"]
            need("build" not in app and app["image"] == pinned_app_image(env["SOURCE_COMMIT"]),
                 "Production profile must select the pinned source image without a build")
            missing_commit = dict(env)
            del missing_commit["SOURCE_COMMIT"]
            result = compose(mode, missing_commit, "--profile", "production", "config", "--quiet")
            need(result.returncode != 0, "Missing SOURCE_COMMIT accepted by production Compose")
        blocked_env = dict(env)
        del blocked_env["POSTGRES_PASSWORD" if mode == "rehearsal" else "JWT_SECRET"]
        result = compose(mode, blocked_env, "config", "--quiet")
        need(result.returncode != 0, f"Missing {mode} required setting accepted")
    print("PASS: Compose renders both files; defaults exclude publisher; production profile selects it only in production; missing required settings rejected")
    compose_path_checks(env)


def environment_checks(mode, check_new):
    for key in names(mode):
        need(bool(os.environ.get(key)), f"Missing required environment variable: {key}")
        need("validation-only" not in os.environ[key], f"Replace synthetic validation value: {key}")
    candidate = os.environ["ARKX_CANDIDATE_ID"]
    need(re.fullmatch(r"[a-z][a-z0-9-]{7,39}", candidate) is not None, "ARKX_CANDIDATE_ID must be a fresh 8-40 character lowercase identifier")
    for key in ("POSTGRES_USER", "POSTGRES_DB", "TEMPORAL_POSTGRES_USER"):
        need(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", os.environ[key]) is not None, f"Unsafe database identifier: {key}")
    for key in ("POSTGRES_PASSWORD", "TEMPORAL_POSTGRES_PASSWORD"):
        need(re.fullmatch(r"[A-Za-z0-9_-]{32,}", os.environ[key]) is not None, f"Use at least 32 random URL-safe characters for {key}")
    if mode == "production":
        pinned_app_image(os.environ["SOURCE_COMMIT"])
        need(os.environ["ARKX_RUNTIME"] == "production", "ARKX_RUNTIME must equal production")
        url = urlsplit(os.environ["ARKX_PUBLIC_URL"])
        need(url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
             and not url.path and not url.query and not url.fragment, "ARKX_PUBLIC_URL must be an HTTPS origin with no trailing slash")
        need(bool(os.environ["JWT_SECRET"].strip()), "Preserve the existing nonblank JWT_SECRET; do not rotate it for migration")
    # Dockerfile.dev copies the source tree. Never let a generated runtime env file enter its context.
    unsafe = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".next", "dist", ".nx"}]
        unsafe.extend(str(Path(base, name).relative_to(ROOT)) for name in files
                      if name.startswith(".env") and name != ".env.example")
    need(not unsafe, "Source context contains non-example .env files; remove them from the build context before using Dockerfile.dev")
    need(shutil.which("docker") is not None, "Docker CLI required for selected-mode rendering")
    result = compose(mode, dict(os.environ, COMPOSE_PROFILES=""), "config", "--quiet")
    need(result.returncode == 0, f"Compose {mode} environment rendering failed (values withheld)")
    if check_new:
        result = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True)
        need(result.returncode == 0, "Cannot read target volume inventory; freshness unproven")
        existing = set(result.stdout.splitlines())
        targets = {f"arkx-postiz-{candidate}-{mode}-{key}" for key in load(mode)["volumes"]}
        need(not targets & existing, "Candidate volume name already exists; choose a new candidate ID, never reuse a production volume")
        print(f"PASS: all {len(targets)} selected-mode volume names absent on current Docker target")
    else:
        print("GAP: target volume freshness not checked; run --check-new-volumes on the intended host before first creation")
    print(f"PASS: {mode} required environment and Compose rendering (values withheld)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("rehearsal", "production"))
    parser.add_argument("--check-new-volumes", action="store_true")
    args = parser.parse_args()
    need(not args.check_new_volumes or args.mode, "--check-new-volumes requires --mode")
    public_build_input_checks()
    static_checks()
    guard_checks()
    ecosystem_checks()
    compose_checks()
    if args.mode:
        environment_checks(args.mode, args.check_new_volumes)
    for name in ("compose.rehearsal.yaml", "compose.production.yaml", "runtime-start.sh", "ecosystem.config.cjs", "dynamicconfig/development-sql.yaml"):
        print(f"SHA256 {name}: {hashlib.sha256((HERE / name).read_bytes()).hexdigest()}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
