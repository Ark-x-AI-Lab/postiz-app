#!/usr/bin/env bash
# Run as root on the existing source host. No arguments; never launches containers.
# Only the exact publisher is stopped/restarted. PostgreSQL/Redis stay running.
# The caller must keep other writers and Coolify reconciliation quiescent.
# /var/backups/arkx-postiz must already be a root-owned, non-writable-by-others directory.
# Partial backups are retained; only a later successful verifier permits restore rehearsal.
# SIGKILL, host loss, or a failed Docker restart still require operator recovery.
set +x
set -eu
exec python3 -I - "$@" <<'PY'
import datetime
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

SOURCE = "8hopq6f9yhiyr8x7nnv6fhnb"
PUBLISHER_IMAGE = "sha256:065d2780396b88c5a03853e912703bca007e28abdab748b4c370457e967064a3"
BASE = Path("/var/backups/arkx-postiz")
NAMES = {role: role + "-" + SOURCE for role in ("postiz", "postgres", "redis")}
VOLUMES = {kind: SOURCE + "_postiz-" + kind for kind in
           ("config", "uploads", "postgresql-data", "redis-data")}
ARTIFACTS = ("postgres.dump", "redis.rdb", "config.tar", "uploads.tar")
# One five-minute acquisition budget, plus a separate bounded restart attempt.
DEADLINE = time.monotonic() + 300
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}


class Refusal(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def run(args, *, output=None, limit=30):
    remaining = DEADLINE - time.monotonic()
    require(remaining > 0, "Backup acquisition deadline reached.")
    try:
        return subprocess.run(args, check=True, stdin=subprocess.DEVNULL,
                              stdout=output if output is not None else subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=ENV,
                              timeout=min(limit, remaining)).stdout
    except (OSError, subprocess.SubprocessError):
        raise Refusal("A required backup command failed or timed out.") from None


def docker(*args, **kwargs):
    return run(["docker", *args], **kwargs)


def inspect_container(name):
    # Never request .Config or .Config.Env; no credential-bearing metadata.
    template = ('{"name":{{json .Name}},"id":{{json .Id}},'
                '"image":{{json .Image}},"state":{{json .State.Status}},'
                '"mounts":{{json .Mounts}}}')
    info = json.loads(docker("container", "inspect", "--format", template, name))
    require(info["name"] == "/" + name, "Source container name mismatch.")
    require(re.fullmatch(r"[0-9a-f]{64}", info["id"]) is not None,
            "Invalid source container identity.")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", info["image"]) is not None,
            "Invalid source image identity.")
    return info


def safe_directory(path, *, volume=False):
    require(path.is_absolute(), "A host path is not absolute.")
    # Check every ancestor too: no symlink or writable path substitution.
    for item in [path, *path.parents]:
        info = item.lstat()
        require(stat.S_ISDIR(info.st_mode), "Unsafe host directory type.")
        # Docker may chown the volume's data directory to the database/app user.
        if item != path or not volume:
            require(info.st_uid == 0 and info.st_mode & 0o022 == 0,
                    "Unsafe host directory ownership or mode.")


@contextmanager
def directory_lock(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal("Another backup holds the backup-directory lock.") from None
        yield
    finally:
        os.close(fd)


def volume_path(name):
    raw = docker("volume", "inspect", "--format",
                 '{"name":{{json .Name}},"driver":{{json .Driver}},'
                 '"options":{{json .Options}},"path":{{json .Mountpoint}}}', name)
    info = json.loads(raw)
    require(info["name"] == name and info["driver"] == "local" and
            not info["options"], "Unexpected source volume or volume driver options.")
    path = Path(info["path"])
    safe_directory(path, volume=True)
    return path


def create_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW, 0o600), "wb")


def finish_file(handle):
    handle.flush()
    os.fsync(handle.fileno())


def interrupted(signum, frame):
    raise Refusal("Backup interrupted; restoring publisher state.")


def restore_publisher(container_id):
    # Always issue start after a stop attempt, including a failed/timed-out stop.
    # Ignore further handled signals until the bounded recovery attempt finishes.
    handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        subprocess.run(["docker", "start", container_id], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=ENV, timeout=60)
        result = subprocess.run(["docker", "container", "inspect", "--format",
                                 "{{.State.Running}}", container_id], check=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=ENV, timeout=15)
        require(result.stdout.strip() == b"true", "Publisher did not return to running.")
    except (OSError, subprocess.SubprocessError):
        raise Refusal("Publisher restart failed; immediate operator recovery required.") from None
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 1,
            "Run as root with no arguments on the existing source host.")
    os.umask(0o077)
    safe_directory(BASE)
    with directory_lock(BASE):
        containers = {role: inspect_container(name) for role, name in NAMES.items()}
        require(containers["postiz"]["image"] == PUBLISHER_IMAGE,
                "Publisher image differs from the confirmed source image.")
        require(containers["postiz"]["state"] in ("running", "exited"),
                "Publisher must be running or cleanly stopped.")
        require(all(containers[role]["state"] == "running" for role in ("postgres", "redis")),
                "Source PostgreSQL and Redis must already be running.")
        paths = {kind: volume_path(name) for kind, name in VOLUMES.items()}
        require(len(set(paths.values())) == 4, "Source volumes do not have distinct paths.")
        expected = {"postiz": {VOLUMES["config"], VOLUMES["uploads"]},
                    "postgres": {VOLUMES["postgresql-data"]},
                    "redis": {VOLUMES["redis-data"]}}
        for role, info in containers.items():
            mounts = [m for m in info["mounts"] if m["Type"] == "volume"]
            require({m["Name"] for m in mounts} == expected[role] and
                    len(mounts) == len(expected[role]), "Source volume allowlist mismatch.")
            for mount in mounts:
                kind = next(k for k, v in VOLUMES.items() if v == mount["Name"])
                require(mount["Source"] == str(paths[kind]), "Source mount path mismatch.")
        redis_mount = next(m for m in containers["redis"]["mounts"]
                           if m.get("Name") == VOLUMES["redis-data"])
        images = {}
        for role, info in containers.items():
            digests = json.loads(docker("image", "inspect", "--format",
                                        "{{json .RepoDigests}}", info["image"])) or []
            require(isinstance(digests, list) and all(isinstance(d, str) and
                    re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}", d)
                    for d in digests), "Invalid image digest metadata.")
            images[role] = {"name": NAMES[role], "id": info["id"],
                            "image_id": info["image"], "repo_digests": digests}
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = BASE / ("postiz-migration-" + stamp)
        destination.mkdir(mode=0o700)  # Exclusive: never reuse or delete a prior backup.
        restart = False
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        try:
            # Recheck identities immediately before the only stop operation.
            for role, name in NAMES.items():
                current = inspect_container(name)
                require(current == containers[role], "Source changed during preflight.")
            if containers["postiz"]["state"] == "running":
                restart = True
                docker("stop", "--time", "30", containers["postiz"]["id"], limit=45)
            stopped = inspect_container(NAMES["postiz"])
            require(stopped["id"] == containers["postiz"]["id"] and
                    stopped["state"] == "exited", "Publisher did not stop.")
            with create_file(destination / "postgres.dump") as handle:
                docker("exec", containers["postgres"]["id"], "sh", "-ceu",
                       'exec pg_dump --no-password --format=custom '
                       '--username="${POSTGRES_USER:?}" --dbname="${POSTGRES_DB:?}"',
                       output=handle, limit=120)
                finish_file(handle)
            # Authenticate only through container-local environment references if set.
            redis_shell = ('if [ -n "${REDIS_PASSWORD:-}" ]; then '
                           'REDISCLI_AUTH="$REDIS_PASSWORD"; export REDISCLI_AUTH; fi; ')
            reply = docker("exec", containers["redis"]["id"], "sh", "-ceu",
                           redis_shell + "redis-cli --raw SAVE", limit=60)
            require(reply.strip() == b"OK", "Redis SAVE did not confirm success.")
            config = docker("exec", containers["redis"]["id"], "sh", "-ceu",
                            redis_shell + "redis-cli --raw CONFIG GET dir dbfilename")
            values = config.decode("utf-8").splitlines()
            require(len(values) == 4, "Redis snapshot location could not be read.")
            settings = dict(zip(values[::2], values[1::2]))
            require(set(settings) == {"dir", "dbfilename"}, "Redis snapshot settings mismatch.")
            require(settings["dir"] == redis_mount["Destination"] and
                    re.fullmatch(r"[A-Za-z0-9_-]+\.rdb", settings["dbfilename"]) is not None,
                    "Redis snapshot must be directly inside its allowlisted volume.")
            # Read the saved RDB through the host mount, not docker cp (which changes modes).
            rdb = paths["redis-data"] / settings["dbfilename"]
            fd = os.open(rdb, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source, create_file(destination / "redis.rdb") as handle:
                require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), "Redis RDB is not a regular file.")
                while True:
                    require(time.monotonic() < DEADLINE, "Backup acquisition deadline reached.")
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                finish_file(handle)
            for kind in ("config", "uploads"):
                safe_directory(paths[kind], volume=True)
                with create_file(destination / (kind + ".tar")) as handle:
                    run(["tar", "--one-file-system", "-C", str(paths[kind]), "-cpf", "-", "."],
                        output=handle, limit=120)
                    finish_file(handle)
            stopped = inspect_container(NAMES["postiz"])
            require(stopped["id"] == containers["postiz"]["id"] and
                    stopped["state"] == "exited", "Publisher state changed during backup.")
        finally:
            if restart:
                restore_publisher(containers["postiz"]["id"])
        entries = []
        for name in ARTIFACTS:
            path = destination / name
            require(path.stat().st_size > 0, "An archive is empty; no completion manifest written.")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    require(time.monotonic() < DEADLINE, "Backup acquisition deadline reached.")
                    digest.update(chunk)
            entries.append({"file": name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
        manifest = {"format": 1, "source_service": SOURCE, "created_utc": stamp,
                    "publisher_originally_running": containers["postiz"]["state"] == "running",
                    "containers": images, "volumes": VOLUMES, "artifacts": entries}
        with create_file(destination / "manifest.json") as handle:
            handle.write((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
            finish_file(handle)
        directory_fd = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        print("Backup acquired; run verify-backup.sh before using it: " + str(destination))


if __name__ == "__main__":
    try:
        main()
    except (Refusal, OSError, ValueError, KeyError, StopIteration, TypeError) as error:
        # No raw exception text: command errors can contain private metadata.
        print("Backup failed. Partial files are retained without a trusted completion claim. "
              "Check the exact publisher's running state before continuing.", file=sys.stderr)
        if isinstance(error, Refusal):
            print(str(error), file=sys.stderr)
        sys.exit(1)
PY
