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
                need(source in data["volumes"] or mount == "./dynamicconfig:/etc/temporal/config/dynamicconfig:ro",
                     f"Unexpected mount: {mode}/{name}")
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
    need(app["build"] == {"context": "../..", "dockerfile": "Dockerfile.dev", "args": {"NEXT_PUBLIC_VERSION": "v2.25.3"}},
         "Build must use unchanged source Dockerfile with only public version argument")
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
        for name in ("nginx", "pnpm"):
            stub = folder / name
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + ' $*" >> "$ARKX_TEST_MARKER"\n')
            stub.chmod(0o700)
        base = {"PATH": f"{folder}:/usr/bin:/bin", "ARKX_TEST_MARKER": str(marker)}
        for mode in (None, "rehearsal", "Production", ""):
            env = dict(base)
            if mode is not None:
                env["ARKX_RUNTIME"] = mode
            result = subprocess.run(["sh", str(HERE / "runtime-start.sh")], env=env, capture_output=True)
            need(result.returncode == 64 and not marker.exists(), "Blocked runtime invoked a publisher command")
        result = subprocess.run(["sh", str(HERE / "runtime-start.sh")], env={**base, "ARKX_RUNTIME": "production"}, capture_output=True)
        need(result.returncode == 0 and marker.read_text().splitlines() == ["nginx ", "pnpm run pm2"],
             "Allowed runtime did not preserve upstream startup command")
    print("PASS: startup guard blocks 4 invalid/missing selections; production reaches stubbed nginx and pnpm only")


def compose(mode, env, *options):
    return subprocess.run(["docker", "compose", "--env-file", os.devnull, "-f", str(HERE / f"compose.{mode}.yaml"),
                           *options], env=env, capture_output=True, text=True)


def compose_checks():
    if not shutil.which("docker"):
        print("GAP: Docker Compose CLI unavailable; rendering/profile checks not run")
        return
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "COMPOSE_PROFILES": ""}
    for key in names("production"):
        env[key] = "synthetic-validation-only"
    env.update(ARKX_CANDIDATE_ID="validation-only", ARKX_RUNTIME="production")
    for mode in ("rehearsal", "production"):
        result = compose(mode, env, "config", "--format", "json")
        need(result.returncode == 0, f"Compose {mode} rendering failed (output withheld)")
        services = json.loads(result.stdout)["services"]
        need("postiz" not in services, f"Default {mode} starts publisher")
        result = compose(mode, env, "--profile", "production", "config", "--format", "json")
        need(result.returncode == 0, f"Compose {mode} profile rendering failed")
        services = json.loads(result.stdout)["services"]
        need(("postiz" in services) == (mode == "production"), "Publisher profile isolation failed")
        blocked_env = dict(env)
        del blocked_env["POSTGRES_PASSWORD" if mode == "rehearsal" else "JWT_SECRET"]
        result = compose(mode, blocked_env, "config", "--quiet")
        need(result.returncode != 0, f"Missing {mode} required setting accepted")
    print("PASS: Compose renders both files; defaults exclude publisher; production profile selects it only in production; missing required settings rejected")


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
    static_checks()
    guard_checks()
    compose_checks()
    if args.mode:
        environment_checks(args.mode, args.check_new_volumes)
    for name in ("compose.rehearsal.yaml", "compose.production.yaml", "runtime-start.sh", "dynamicconfig/development-sql.yaml"):
        print(f"SHA256 {name}: {hashlib.sha256((HERE / name).read_bytes()).hexdigest()}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
