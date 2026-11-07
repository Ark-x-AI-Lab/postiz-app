#!/usr/bin/env python3
"""Pinned current-app cold acquisition only. Default: read-only plan, never restore."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import tarfile
import time

APP = "1alwcyoouzoa5mf1jrnfzm87"
SOURCE = "722588393220efc293453f7c1273c0037d6fe59d"
PROJECT = "lozfyzlfbmts1wnihwefd4l8"
PUBLISHER_IMAGE = "sha256:b8e8c7e2c82a94b337ebdf69c161d8524c5d964db315c9878b977ec3338d240b"
IDS = dict(zip(("postiz", "postiz-postgres", "postiz-redis", "temporal",
                "temporal-postgresql", "temporal-elasticsearch", "temporal-admin-tools", "temporal-ui"), (
    "0e4dd8a7023ebd98330ea2879e136cc8c0bbab8f12626aa80bb495646cff6402",
    "0c4dd160d9511325d5571647bc3b0f06038c81c5ed21171eaae5c3d1bc01d1e0",
    "ea4601b1b2d002fb3dda2a818fc449a55a23cb8f7ba7a6ee5311c0dff6243be9",
    "51142d926518e2576f85b2728111d1973f393066a0c888a37534530beb4a6491",
    "26ae6c6873b8e28fd851d2cff1c98ead577da1f5fc5de5809a3a13b92f96575a",
    "471833b1283c69b9accedf8ab8ae7a3c6b5b92e30e52e902736e8c4371eac425",
    "49fa37c262111e3159f2c4e4156f27f57f1abfa4ebbe8ccf879b411009c7c100",
    "c343745081a087d3ca1c1a3cae7555a335135c4a8158b7d31dd1464b55150239")))
REFERENCES = {"postiz-postgres": "postgres:14.5", "postiz-redis": "redis:7.2",
              "temporal-postgresql": "postgres:16", "temporal-elasticsearch": "elasticsearch:7.17.27",
              "temporal": "temporalio/auto-setup:1.28.1",
              "temporal-admin-tools": "temporalio/admin-tools:1.28.1-tctl-1.18.4-cli-1.4.1",
              "temporal-ui": "temporalio/ui:2.34.0"}
VOLUMES = {"postiz-config": ("postiz", "/config"), "postiz-uploads": ("postiz", "/uploads"),
           "postiz-postgres": ("postiz-postgres", "/var/lib/postgresql/data"),
           "postiz-redis": ("postiz-redis", "/data"),
           "temporal-postgresql": ("temporal-postgresql", "/var/lib/postgresql/data"),
           "temporal-elasticsearch": ("temporal-elasticsearch", "/usr/share/elasticsearch/data")}
START = ("postiz-postgres", "postiz-redis", "temporal-postgresql", "temporal-elasticsearch",
         "temporal", "postiz")
STATELESS = ("temporal-admin-tools", "temporal-ui")
BASE = Path("/var/backups/arkx-postiz/current-app")
FS_ROOT = Path("/")  # Only the offline fixture supplies a disposable filesystem root.
OWNER = 0
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Refusal(Exception):
    """No provider/command/filename diagnostics escape this boundary."""


def need(value):
    if not value:
        raise Refusal("Current-app backup refused.")


def acquisition_checkpoint(adapter):
    """Cancellation raises synchronously here, never from a signal callback."""
    if getattr(adapter, "_signal_state", {}).get("pending", False):
        raise InterruptedError()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def volume_path(suffix):
    return Path("/var/lib/docker/volumes") / (APP + "_" + suffix) / "_data"


def name(role):
    return role + "-" + APP + "-20261106T173519"


def fingerprint(fd):
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)]


def open_dir(path, volume=False):
    """Open every component relative to held descriptors; never follow symlinks."""
    need(path.is_absolute() and ".." not in path.parts)
    fd = os.open(FS_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        need(os.fstat(fd).st_uid == OWNER and not os.fstat(fd).st_mode & 0o022)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if not (volume and part == path.parts[-1]):
                need(info.st_uid == OWNER and not info.st_mode & 0o022)
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_file(fd):
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER
         and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


def exclusive(directory, filename):
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        private_file(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def backup_base(create=False):
    parent = open_dir(BASE.parent)
    try:
        if create:
            try:
                os.mkdir(BASE.name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        try:
            fd = os.open(BASE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return None, {"parent": fingerprint(parent), "absent": True}
        info = os.fstat(fd)
        if info.st_uid != OWNER or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise Refusal()
        return fd, {"parent": fingerprint(parent), "directory": fingerprint(fd)}
    finally:
        os.close(parent)


class Docker:
    """Whitelisted projections only; never inspect Config/Env, logs or credentials."""
    def uid(self):
        return os.geteuid()

    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def stamp(self):
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")

    def call(self, args, timeout=15, output=None, pass_fds=()):
        try:
            return subprocess.run(args, check=True, stdin=subprocess.DEVNULL,
                                  stdout=output if output is not None else subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=timeout, pass_fds=pass_fds,
                                  env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                                       "HOME": "/nonexistent"}).stdout
        except (OSError, subprocess.SubprocessError):
            raise Refusal() from None

    def docker(self, *args):
        return self.call(["/usr/bin/docker", "--config", "/nonexistent/arkx-current-backup",
                          "--host", "unix:///var/run/docker.sock", *args])

    def members(self):
        return self.docker("container", "ls", "--all", "--quiet", "--no-trunc",
                           "--filter", "label=com.docker.compose.project=" + PROJECT).decode().splitlines()

    def inspect(self, role):
        template = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
                    '"reference":{{json .Config.Image}},'
                    '"restart":{{json .HostConfig.RestartPolicy}},'
                    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                    '"started":{{json .State.StartedAt}},"status":{{json .State.Status}},'
                    '"running":{{json .State.Running}},"exit":{{json .State.ExitCode}},'
                    '"oom":{{json .State.OOMKilled}},'
                    '"health":{{with index .State "Health"}}{{json .Status}}{{else}}""{{end}},'
                    '"mounts":{{json .Mounts}}}')
        return json.loads(self.docker("container", "inspect", "--format", template, IDS[role]))

    def volume(self, suffix):
        template = ('{"name":{{json .Name}},"driver":{{json .Driver}},'
                    '"path":{{json .Mountpoint}},"plain":{{if .Options}}false{{else}}true{{end}}}')
        return json.loads(self.docker("volume", "inspect", "--format", template, APP + "_" + suffix))

    def consumers(self):
        # A name-only volume filter misses bind aliases and binds of a parent.
        # Read only IDs + mount metadata; never operate on another container.
        ids = self.docker("container", "ls", "--all", "--quiet", "--no-trunc").decode().splitlines()
        need(len(ids) <= 512 and len(set(ids)) == len(ids)
             and all(re.fullmatch(r"[0-9a-f]{64}", item) for item in ids))
        result = {suffix: [] for suffix in VOLUMES}
        seen = set()
        template = '{"id":{{json .Id}},"mounts":{{json .Mounts}}}'
        for start in range(0, len(ids), 32):
            rows = self.docker("container", "inspect", "--format", template, *ids[start:start + 32])
            for line in rows.decode().splitlines():
                item = json.loads(line)
                need(item["id"] in ids and item["id"] not in seen)
                seen.add(item["id"])
                for suffix in VOLUMES:
                    root = str(volume_path(suffix))
                    for mount in item["mounts"]:
                        source = mount.get("Source", "")
                        need(isinstance(source, str))
                        source = os.path.normpath("/" + source.lstrip("/")) if source.startswith("/") else ""
                        overlap = bool(source) and mount.get("Type") in ("bind", "volume") and (
                            source == root or source == "/" or source.startswith(root + "/") or root.startswith(source + "/"))
                        if mount.get("Name") == APP + "_" + suffix or overlap:
                            result[suffix].append(item["id"])
                            break
        need(seen == set(ids))
        return result

    def submounts(self):
        roots = [str(volume_path(s)) + "/" for s in VOLUMES]
        with open("/proc/self/mountinfo", encoding="utf-8") as source:
            for line in source:
                target = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
                need(not any(target.startswith(root) for root in roots))

    def terminate(self, role):
        need(role in START)
        # Docker stop with a finite timeout may send SIGKILL. Never use it here.
        self.docker("container", "kill", "--signal=SIGTERM", IDS[role])

    def start(self, role):
        need(role in START)
        self.docker("container", "start", IDS[role])

    def archive(self, source, output, remaining):
        self.call(["/usr/bin/tar", "--one-file-system", "--numeric-owner", "--format=pax",
                   "-C", "/proc/self/fd/" + str(source), "-cpf", "-", "."],
                  timeout=min(120, remaining), output=output, pass_fds=(source,))

    @contextlib.contextmanager
    def signals(self, recovering=False):
        if not recovering:
            self._signal_state = {"pending": False}
        state = self._signal_state

        def interrupted(signum, frame):
            state["pending"] = True

        def deferred_interrupt(signum, frame):
            state["pending"] = True

        handlers = {}
        previous_mask = None
        setup_failed = False

        def transition(operation, *args):
            nonlocal setup_failed
            # Every setup/cleanup API, including lookup and masking, has the
            # same bounded handling. Failure must never gate recovery's yield.
            for _ in range(3):
                try:
                    return operation(*args)
                except (InterruptedError, KeyboardInterrupt):
                    state["pending"] = True
                except Exception:
                    setup_failed = True
                    return None
            setup_failed = True
            return None

        try:
            if recovering:
                previous_mask = transition(signal.pthread_sigmask, signal.SIG_BLOCK, SIGNALS)
            for sig in SIGNALS:
                if not recovering:
                    handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, interrupted)
                    continue
                old_handler = transition(signal.getsignal, sig)
                if old_handler is not None:
                    handlers[sig] = old_handler
                    transition(signal.signal, sig, deferred_interrupt)
            if recovering and previous_mask is not None and not setup_failed:
                transition(signal.pthread_sigmask, signal.SIG_SETMASK, previous_mask)
            # Both old and new callbacks only record cancellation, including at
            # generator entry. Setup cannot turn that callback into an unwind.
            yield
        finally:
            if recovering:
                cleanup_mask = transition(signal.pthread_sigmask, signal.SIG_BLOCK, SIGNALS)
                if previous_mask is None:
                    previous_mask = cleanup_mask
            for sig, handler in handlers.items():
                transition(signal.signal, sig, handler)
            if previous_mask is not None:
                transition(signal.pthread_sigmask, signal.SIG_SETMASK, previous_mask)
        if setup_failed:
            raise Refusal() from None


def identity(info, role):
    need(info["id"] == IDS[role] and info["name"] == "/" + name(role)
         and info["project"] == PROJECT and info["service"] == role)
    need(re.fullmatch(r"sha256:[0-9a-f]{64}", info["image"]) is not None)
    need(info["restart"] == {"Name": "unless-stopped", "MaximumRetryCount": 0})
    if role == "postiz":
        need(info["image"] == PUBLISHER_IMAGE)
        need(re.fullmatch(r"ghcr\.io/ark-x-ai-lab/postiz-app(?::sha-[0-9a-f]{40}|@sha256:[0-9a-f]{64})", info["reference"]) is not None)
    else:
        need(info["reference"] == REFERENCES[role])
    expected = sorted([{"Type": "volume", "Name": APP + "_" + suffix,
                        "Source": str(volume_path(suffix)), "Destination": dest, "RW": True}
                       for suffix, (writer, dest) in VOLUMES.items() if writer == role], key=lambda m: m["Name"])
    actual = sorted([{k: mount[k] for k in ("Type", "Name", "Source", "Destination", "RW")}
                     for mount in info["mounts"]], key=lambda m: m["Name"])
    need(actual == expected)
    return {k: info[k] for k in ("id", "name", "image", "reference", "restart", "project", "service", "started")} | {"mounts": actual}


def unchanged_stateless(adapter, pinned):
    for role in STATELESS:
        info = adapter.inspect(role)
        need(info["status"] == "running" and info["running"] is True)
        need(identity(info, role) == pinned["containers"][role])


def collect(adapter, stopped=False, pinned=None, cancellation=True):
    if cancellation:
        acquisition_checkpoint(adapter)
    need(sorted(adapter.members()) == sorted(IDS.values()))
    containers, volumes = {}, {}
    for role in IDS:
        if cancellation:
            acquisition_checkpoint(adapter)
        info = adapter.inspect(role)
        cold_writer = stopped and role in START
        need(info["status"] == ("exited" if cold_writer else "running") and info["running"] is (not cold_writer))
        if cold_writer:
            need(info["exit"] in (0, 143) and not info["oom"])
        containers[role] = identity(info, role)
        if pinned is not None and role in STATELESS:
            need(containers[role] == pinned["containers"][role])
    adapter.submounts()
    consumers = adapter.consumers()
    for suffix, (role, _) in VOLUMES.items():
        if cancellation:
            acquisition_checkpoint(adapter)
        need(adapter.volume(suffix) == {"name": APP + "_" + suffix, "driver": "local",
                                        "path": str(volume_path(suffix)), "plain": True})
        need(consumers[suffix] == [IDS[role]])
        fd = open_dir(volume_path(suffix), volume=True)
        try:
            volumes[suffix] = {"name": APP + "_" + suffix, "path": str(volume_path(suffix)), "directory": fingerprint(fd)}
        finally:
            os.close(fd)
    return {"app": APP, "source": SOURCE, "project": PROJECT, "containers": containers, "volumes": volumes,
            "stop_order": list(reversed(START)), "start_order": list(START), "kept_running": list(STATELESS)}


def wait(adapter, role, running, deadline, pinned, same_start=False):
    while True:
        info = adapter.inspect(role)
        current = identity(info, role)
        if same_start:
            need(current["started"] == pinned["started"])
        need({k: v for k, v in current.items() if k != "started"}
             == {k: v for k, v in pinned.items() if k != "started"})
        if running and info["running"] and info["status"] == "running" and info["health"] in ("", "healthy"):
            return
        if not running and info["status"] == "exited" and not info["running"]:
            need(info["exit"] in (0, 143) and not info["oom"])
            return
        need(adapter.now() < deadline)
        adapter.sleep(1)


def check_archive(directory, filename, adapter, deadline):
    acquisition_checkpoint(adapter)
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        private_file(fd)
        size = os.fstat(fd).st_size
        need(size >= 1024 and size % 512 == 0)
        with os.fdopen(os.dup(fd), "rb") as handle:
            with tarfile.open(fileobj=handle, mode="r:") as archive:
                count = 0
                regular = set()
                names = set()
                for member in archive:
                    acquisition_checkpoint(adapter)
                    need(adapter.now() < deadline)
                    path = PurePosixPath(member.name)
                    need(not path.is_absolute() and ".." not in path.parts
                         and not any(ord(c) < 32 or ord(c) == 127 for c in member.name)
                         and (member.isfile() or member.isdir() or member.islnk()))
                    need(path not in names)
                    names.add(path)
                    need(member.offset_data + ((member.size + 511) // 512) * 512 <= size - 1024)
                    if member.islnk():
                        target = PurePosixPath(member.linkname)
                        need(not target.is_absolute() and ".." not in target.parts and target in regular
                             and not any(ord(c) < 32 or ord(c) == 127 for c in member.linkname))
                    if member.isfile():
                        regular.add(path)
                    count += 1
                    need(count <= 1_000_000)
                end = archive.offset
            need(count > 0 and size - end >= 1024)
            handle.seek(end)
            while chunk := handle.read(1024 * 1024):
                acquisition_checkpoint(adapter)
                need(adapter.now() < deadline and not any(chunk))
            handle.seek(0)
            checksum = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                acquisition_checkpoint(adapter)
                need(adapter.now() < deadline)
                checksum.update(chunk)
        return {"file": filename, "bytes": size, "sha256": checksum.hexdigest()}
    finally:
        os.close(fd)


def verify(directory, entries, adapter, deadline):
    acquisition_checkpoint(adapter)
    need(len(entries) == 6 and {e["file"] for e in entries} == {s + ".tar" for s in VOLUMES})
    for entry in entries:
        need(check_archive(directory, entry["file"], adapter, deadline) == entry)


def bound_backup(base, output, directory_name):
    """Held descriptors remain authoritative; the printed logical path must agree."""
    current, _ = backup_base()
    need(current is not None)
    try:
        need(fingerprint(current) == fingerprint(base))
        child = os.open(directory_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        try:
            need(fingerprint(child) == fingerprint(output)
                 and os.fstat(child).st_uid == OWNER and stat.S_IMODE(os.fstat(child).st_mode) == 0o700)
        finally:
            os.close(child)
    finally:
        os.close(current)


def acquire(adapter, approved_hash=None, execute=False):
    """Adapters are for offline tests; the CLI exposes no retargeting controls."""
    need(adapter.uid() == 0)
    plan = collect(adapter)
    base, base_identity = backup_base()
    if base is not None:
        os.close(base)
    plan["backup_root"] = {"path": str(BASE), **base_identity}
    plan["helper_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    plan_hash = digest(plan)
    if not execute:
        return {"status": "dry-run plan-sha256=" + plan_hash, "backup_path": None}
    need(isinstance(approved_hash, str) and re.fullmatch(r"[0-9a-f]{64}", approved_hash) and approved_hash == plan_hash)
    backup_fd, _ = backup_base(create=True)
    need(backup_fd is not None)
    lock = None
    destination = None
    attempted, entries = set(), []
    failure, recovery_failed = False, False
    deadline = adapter.now() + 1200
    try:
        lock = os.open(".backup.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=backup_fd)
        private_file(lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck source and directory identities under the lock before any signal.
        need(collect(adapter) == {k: v for k, v in plan.items() if k not in ("backup_root", "helper_sha256")})
        current_base, current_identity = backup_base()
        try:
            need(current_base is not None and fingerprint(current_base) == fingerprint(backup_fd))
            if "directory" in base_identity:
                need(current_identity == base_identity)
            else:
                need(current_identity["parent"] == base_identity["parent"])
        finally:
            if current_base is not None:
                os.close(current_base)
        stamp = adapter.stamp()
        need(re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z", stamp))
        directory_name = "cold-" + stamp
        os.mkdir(directory_name, 0o700, dir_fd=backup_fd)
        destination = str(BASE / directory_name)
        output = os.open(directory_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=backup_fd)
        try:
            with adapter.signals():
                try:
                    acquisition_checkpoint(adapter)
                    for role in reversed(START):
                        acquisition_checkpoint(adapter)
                        need(adapter.now() < deadline)
                        bound_backup(backup_fd, output, directory_name)
                        unchanged_stateless(adapter, plan)
                        need(identity(adapter.inspect(role), role) == plan["containers"][role])
                        acquisition_checkpoint(adapter)
                        attempted.add(role)  # Before signaling: even an ambiguous failure must recover.
                        adapter.terminate(role)
                        wait(adapter, role, False, min(deadline, adapter.now() + 45), plan["containers"][role])
                        # Finish this bounded graceful stop before cancellation
                        # can transfer control to its matching restart attempt.
                        acquisition_checkpoint(adapter)
                    source_plan = {k: v for k, v in plan.items() if k not in ("backup_root", "helper_sha256")}
                    for suffix in VOLUMES:
                        acquisition_checkpoint(adapter)
                        bound_backup(backup_fd, output, directory_name)
                        need(collect(adapter, stopped=True, pinned=plan) == source_plan)
                        source = open_dir(volume_path(suffix), volume=True)
                        try:
                            need(fingerprint(source) == plan["volumes"][suffix]["directory"])
                            file_fd = exclusive(output, suffix + ".tar")
                            try:
                                need(adapter.now() < deadline)
                                acquisition_checkpoint(adapter)
                                adapter.archive(source, file_fd, deadline - adapter.now())
                                acquisition_checkpoint(adapter)
                                os.fsync(file_fd)
                            finally:
                                os.close(file_fd)
                        finally:
                            os.close(source)
                        entries.append(check_archive(output, suffix + ".tar", adapter, deadline))
                    need(collect(adapter, stopped=True, pinned=plan) == source_plan)
                    verify(output, entries, adapter, deadline)
                    acquisition_checkpoint(adapter)
                except (Exception, KeyboardInterrupt):
                    failure = True
                finally:
                    # Fail closed until every required recovery attempt completes.
                    recovery_failed = bool(attempted)
                    with adapter.signals(recovering=True):
                        restart_failed = False
                        recovery_deadline = adapter.now() + 480
                        for role in START:
                            if role in attempted:
                                try:
                                    current = identity(adapter.inspect(role), role)
                                    need({k: v for k, v in current.items() if k != "started"}
                                         == {k: v for k, v in plan["containers"][role].items() if k != "started"})
                                    adapter.start(role)
                                    wait(adapter, role, True, min(recovery_deadline, adapter.now() + 45), plan["containers"][role])
                                except (Exception, KeyboardInterrupt):
                                    restart_failed = True
                        # Never start stateless roles, even if their state has
                        # drifted. Check every original's readiness and retain
                        # the stateless/unattempted start-time identity.
                        for role in IDS:
                            try:
                                wait(adapter, role, True, min(recovery_deadline, adapter.now() + 45),
                                     plan["containers"][role], same_start=role not in attempted)
                            except (Exception, KeyboardInterrupt):
                                restart_failed = True
                        try:
                            restored = collect(adapter, pinned=plan, cancellation=False)
                            need(restored["volumes"] == plan["volumes"])
                            for role in IDS:
                                actual = restored["containers"][role]
                                expected = plan["containers"][role]
                                ignored = {"started"} if role in attempted else set()
                                need({k: v for k, v in actual.items() if k not in ignored}
                                     == {k: v for k, v in expected.items() if k not in ignored})
                        except (Exception, KeyboardInterrupt):
                            restart_failed = True
                        recovery_failed = restart_failed
            # Outside recovery: a pending signal prevents any success manifest,
            # but can no longer prevent or interrupt its restart loop.
            acquisition_checkpoint(adapter)
            if not failure and not recovery_failed:
                bound_backup(backup_fd, output, directory_name)
                manifest = {"format": 1, "kind": "cold-volume-acquisition", "created_utc": stamp,
                            "plan_hash": plan_hash, "plan": plan, "artifacts": entries,
                            "archives_verified": True, "writer_containers_restarted": True,
                            "all_containers_healthy": True, "stateless_containers_untouched": True,
                            "isolated_restore_proven": False}
                manifest_fd = exclusive(output, "manifest.json")
                try:
                    with os.fdopen(os.dup(manifest_fd), "wb") as handle:
                        handle.write(canonical(manifest) + b"\n")
                        handle.flush()
                    os.fsync(manifest_fd)
                    os.fsync(output)
                    os.fsync(backup_fd)
                finally:
                    os.close(manifest_fd)
        finally:
            os.close(output)
    except (Exception, KeyboardInterrupt):
        failure = True
    finally:
        if lock is not None:
            os.close(lock)
        os.close(backup_fd)
    return {"status": "recovery-required" if recovery_failed else "partial-retained" if failure else "cold-backup-verified",
            "backup_path": destination}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved-plan-hash")
    args = parser.parse_args()
    try:
        need(os.geteuid() == 0)
        os.umask(0o077)
        result = acquire(Docker(), args.approved_plan_hash, args.execute)
    except (Exception, KeyboardInterrupt):
        result = {"status": "refused", "backup_path": None}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"].startswith("dry-run ") or result["status"] == "cold-backup-verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
