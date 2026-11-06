#!/usr/bin/env bash
# Read-only, root-only verification. Usage: verify-backup.sh /var/backups/arkx-postiz/postiz-migration-<UTC>
# Catalog/hash checks are not an actual restore, row comparison, or login test.
set +x
set -eu
exec python3 -I - "$@" <<'PY'
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time

SOURCE = "8hopq6f9yhiyr8x7nnv6fhnb"
PUBLISHER_IMAGE = "sha256:065d2780396b88c5a03853e912703bca007e28abdab748b4c370457e967064a3"
BASE = Path("/var/backups/arkx-postiz")
ARTIFACTS = ("postgres.dump", "redis.rdb", "config.tar", "uploads.tar")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
DEADLINE = time.monotonic() + 300


class Refusal(Exception):
    pass


def require(condition):
    if not condition:
        raise Refusal()


def check_time():
    require(time.monotonic() < DEADLINE)


def safe_file(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


def command(args, *, source=None):
    check_time()
    return subprocess.run(args, check=True, stdin=source if source is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV,
                          timeout=min(60, DEADLINE - time.monotonic())).stdout


def tar_count(path):
    count = 0
    # Require complete tar framing as well as valid headers; never extract data.
    with path.open("rb") as handle:
        size = path.stat().st_size
        require(size >= 1024 and size % 512 == 0)
        handle.seek(-1024, os.SEEK_END)
        require(handle.read(1024) == b"\0" * 1024)
    # Streaming header inspection; nothing is extracted and no member name is printed.
    with tarfile.open(path, mode="r|") as archive:
        for member in archive:
            check_time()
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts and
                    not any(ord(c) < 32 or ord(c) == 127 for c in member.name) and
                    (member.isfile() or member.isdir() or member.issym() or member.islnk()))
            if member.issym() or member.islnk():
                target = PurePosixPath(member.linkname)
                require(not target.is_absolute() and
                        not any(ord(c) < 32 or ord(c) == 127 for c in member.linkname))
                parts = list(name.parent.parts) if member.issym() else []
                for part in target.parts:
                    if part == "..":
                        require(bool(parts))
                        parts.pop()
                    elif part != ".":
                        parts.append(part)
            count += 1
    require(count > 0)
    return count


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 2)
    directory = Path(sys.argv[1])
    require(directory.parent == BASE and
            re.fullmatch(r"postiz-migration-\d{8}T\d{6}\.\d{6}Z", directory.name))
    for item in [directory, *directory.parents]:
        info = item.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o022 == 0)
    require(stat.S_IMODE(directory.stat().st_mode) == 0o700)
    require({p.name for p in directory.iterdir()} == {*ARTIFACTS, "manifest.json"})
    safe_file(directory / "manifest.json")
    require((directory / "manifest.json").stat().st_size < 65536)
    manifest = json.loads((directory / "manifest.json").read_text())
    require(manifest["format"] == 1 and manifest["source_service"] == SOURCE and
            directory.name == "postiz-migration-" + manifest["created_utc"] and
            type(manifest["publisher_originally_running"]) is bool)
    require(manifest["volumes"] == {kind: SOURCE + "_postiz-" + kind for kind in
                                    ("config", "uploads", "postgresql-data", "redis-data")})
    require(set(manifest["containers"]) == {"postiz", "postgres", "redis"})
    require(manifest["containers"]["postiz"]["image_id"] == PUBLISHER_IMAGE)
    for role, info in manifest["containers"].items():
        require(info["name"] == role + "-" + SOURCE and
                re.fullmatch(r"[0-9a-f]{64}", info["id"]) and
                re.fullmatch(r"sha256:[0-9a-f]{64}", info["image_id"]) and
                isinstance(info["repo_digests"], list) and
                all(re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}", d)
                    for d in info["repo_digests"]))
    entries = manifest["artifacts"]
    require(isinstance(entries, list) and len(entries) == 4 and
            {entry["file"] for entry in entries} == set(ARTIFACTS))
    total_bytes = 0
    for entry in entries:
        require(set(entry) == {"file", "bytes", "sha256"} and
                type(entry["bytes"]) is int and entry["bytes"] > 0 and
                re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]))
        path = directory / entry["file"]
        safe_file(path)
        require(path.stat().st_size == entry["bytes"])
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                check_time()
                digest.update(chunk)
        require(digest.hexdigest() == entry["sha256"])
        total_bytes += entry["bytes"]
    with (directory / "postgres.dump").open("rb") as handle:
        require(handle.read(5) == b"PGDMP")
    with (directory / "redis.rdb").open("rb") as handle:
        header = handle.read(9)
        require(re.fullmatch(rb"REDIS[0-9]{4}", header) and
                (directory / "redis.rdb").stat().st_size >= 18)
    counts = {kind: tar_count(directory / (kind + ".tar")) for kind in ("config", "uploads")}
    # Reuse only the exact existing PostgreSQL container/image; no helper container.
    postgres = manifest["containers"]["postgres"]
    template = ('{"name":{{json .Name}},"id":{{json .Id}},'
                '"image":{{json .Image}},"running":{{json .State.Running}}}')
    live = json.loads(command(["docker", "container", "inspect", "--format", template,
                               postgres["name"]]))
    require(live == {"name": "/" + postgres["name"], "id": postgres["id"],
                     "image": postgres["image_id"], "running": True})
    with (directory / "postgres.dump").open("rb") as handle:
        catalog = command(["docker", "exec", "-i", postgres["id"],
                           "pg_restore", "--list"], source=handle)
    catalog_count = sum(1 for line in catalog.splitlines() if line.strip() and not line.startswith(b";"))
    require(catalog_count > 0)
    print(f"Archive checks passed: 4 files, {total_bytes} bytes; "
          f"database catalog entries={catalog_count}; config entries={counts['config']}; "
          f"uploads entries={counts['uploads']}. No data extracted or rows printed.")
    print("Still required: isolated database/Redis restore, source/restored counts and hashes, "
          "preserved signing configuration, old-version recovery, and login proof.")


if __name__ == "__main__":
    try:
        main()
    except (Refusal, OSError, ValueError, KeyError, TypeError, tarfile.TarError,
            subprocess.SubprocessError):
        print("Backup verification failed. No data was extracted or changed.", file=sys.stderr)
        sys.exit(1)
PY
