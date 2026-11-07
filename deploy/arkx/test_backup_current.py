"""Offline only: temporary filesystem, fake Docker/clock, no subprocess transport."""
import contextlib
import copy
import importlib.util
import io
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backup_current", Path(__file__).with_name("backup-current.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
PRIVATE = "SYNTHETIC_PRIVATE_NOT_FOR_OUTPUT"


class Fake:
    def __init__(self):
        self.clock, self.events, self.stall = 0, [], set()
        self.stop_error = self.archive_error = self.start_error = None
        self.archive_count = 0
        self.inspections = []
        self.cold_snapshots = []
        self.archive_kind = "valid"
        self.hook = None
        self.root_uid = 0
        self.info = {}
        for index, role in enumerate(b.IDS):
            self.info[role] = {"id": b.IDS[role], "name": "/" + b.name(role), "project": b.PROJECT,
                               "service": role, "image": b.PUBLISHER_IMAGE if role == "postiz" else "sha256:" + f"{index:064x}",
                               "reference": "ghcr.io/ark-x-ai-lab/postiz-app:sha-" + "d" * 40 if role == "postiz" else b.REFERENCES[role],
                               "started": "2026-11-06T17:35:19Z", "status": "running", "running": True,
                               "restart": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                               "exit": 0, "oom": False, "health": "", "mounts": []}
        for suffix, (role, dest) in b.VOLUMES.items():
            self.info[role]["mounts"].append({"Type": "volume", "Name": b.APP + "_" + suffix,
                                              "Source": str(b.volume_path(suffix)), "Destination": dest, "RW": True})
        self.member_ids = list(b.IDS.values())
        self.stateless_started = {role: self.info[role]["started"] for role in b.STATELESS}
        self.volume_info = {s: {"name": b.APP + "_" + s, "driver": "local", "path": str(b.volume_path(s)), "plain": True} for s in b.VOLUMES}
        self.volume_users = {s: [b.IDS[role]] for s, (role, _) in b.VOLUMES.items()}

    def uid(self): return self.root_uid
    def now(self): return self.clock
    def sleep(self, seconds): self.clock += seconds
    def stamp(self): return "20261107T010203.000004Z"
    def members(self): return self.member_ids[:]
    def inspect(self, role):
        assert role in b.IDS
        self.inspections.append((sum(e[0] == "start" for e in self.events), role))
        return copy.deepcopy(self.info[role])
    def volume(self, suffix):
        assert suffix in b.VOLUMES
        return copy.deepcopy(self.volume_info[suffix])
    def consumers(self):
        return copy.deepcopy(self.volume_users)
    def submounts(self): pass

    def terminate(self, role):
        self.events.append(("term", role))
        assert role in b.START, "Stateless containers must never be signaled"
        if role not in self.stall:
            self.info[role].update(status="exited", running=False)
        if self.hook:
            self.hook(role)
        if self.stop_error == role:
            raise OSError(PRIVATE)

    def start(self, role):
        self.events.append(("start", role))
        assert role in b.START, "Stateless containers must never be started"
        if self.start_error == role:
            raise OSError(PRIVATE)
        self.info[role].update(status="running", running=True, started="restarted")

    @contextlib.contextmanager
    def signals(self, recovering=False):
        yield

    def archive(self, source, output, remaining):
        assert remaining > 0
        assert all(not self.info[role]["running"] for role in b.START), "No active writer during archive"
        assert all(self.info[role]["running"] and not self.info[role]["mounts"]
                   and self.info[role]["started"] == self.stateless_started[role] for role in b.STATELESS)
        assert len([e for e in self.events if e[0] == "term"]) == 6
        self.cold_snapshots.append({role: info["running"] for role, info in self.info.items()})
        assert stat.S_ISDIR(os.fstat(source).st_mode)
        self.archive_count += 1
        self.events.append(("archive", self.archive_count))
        if self.archive_error == "interrupt": raise KeyboardInterrupt(PRIVATE)
        if self.archive_error == "failure": raise OSError(PRIVATE)
        with os.fdopen(os.dup(output), "wb") as handle:
            if self.archive_kind == "corrupt":
                handle.write(b"bad" * 1024)
                return
            with tarfile.open(fileobj=handle, mode="w", format=tarfile.PAX_FORMAT) as archive:
                member = tarfile.TarInfo({"absolute": "/private", "traversal": "../private"}.get(self.archive_kind, "synthetic"))
                if self.archive_kind in ("symlink", "hardlink"):
                    member.type = tarfile.SYMTYPE if self.archive_kind == "symlink" else tarfile.LNKTYPE
                    member.linkname = "../../outside"
                elif self.archive_kind == "special":
                    member.type = tarfile.FIFOTYPE
                else:
                    member.size = len(PRIVATE)
                archive.addfile(member, io.BytesIO(PRIVATE.encode()) if member.isfile() else None)


class CurrentBackup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="arkx-cold-offline-")
        self.root = Path(self.temporary.name).resolve()
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(b, "FS_ROOT", self.root))
        self.stack.enter_context(patch.object(b, "OWNER", os.getuid()))
        self.stack.enter_context(patch.object(b.subprocess, "run", side_effect=AssertionError("Native subprocess forbidden")))
        for path in [b.BASE.parent, *[b.volume_path(s) for s in b.VOLUMES]]:
            self.physical(path).mkdir(parents=True, mode=0o700)
        self.fake = Fake()

    def tearDown(self):
        try:
            self.assertFalse(any(kind in ("term", "start") and role in b.STATELESS for kind, role in self.fake.events))
        finally:
            self.stack.close()
            self.temporary.cleanup()

    def physical(self, logical): return self.root.joinpath(*logical.parts[1:])
    def plan_hash(self): return b.acquire(self.fake)["status"].split("=", 1)[1]
    def execute(self, approved=None): return b.acquire(self.fake, approved or self.plan_hash(), True)

    def assert_refused(self):
        with self.assertRaises(Exception): b.acquire(self.fake)
        self.assertEqual(self.fake.events, [])

    def test_default_dry_run_has_no_mutations(self):
        result = b.acquire(self.fake)
        self.assertRegex(result["status"], r"^dry-run plan-sha256=[a-f0-9]{64}$")
        self.assertIsNone(result["backup_path"])
        self.assertEqual(self.fake.events, [])
        self.assertFalse(self.physical(b.BASE).exists())
        self.assertEqual(result, b.acquire(self.fake))
        self.assertEqual(result, b.acquire(self.fake, "f" * 64))

    def test_healthy_execution_permissions_order_and_manifest(self):
        self.assertTrue(all(info["running"] for info in self.fake.info.values()))
        approved = self.plan_hash()
        result = self.execute(approved)
        self.assertEqual(result["status"], "cold-backup-verified")
        directory = self.physical(Path(result["backup_path"]))
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual({p.name for p in directory.iterdir()}, {s + ".tar" for s in b.VOLUMES} | {"manifest.json"})
        for path in directory.iterdir(): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(manifest["plan_hash"], approved)
        self.assertEqual(set(manifest["plan"]["containers"]), set(b.IDS))
        self.assertFalse(manifest["isolated_restore_proven"])
        self.assertEqual(manifest["plan"]["kept_running"], list(b.STATELESS))
        self.assertTrue(manifest["writer_containers_restarted"])
        self.assertTrue(manifest["all_containers_healthy"])
        self.assertTrue(manifest["stateless_containers_untouched"])
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "term"], list(reversed(b.START)))
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], list(b.START))
        self.assertTrue(all(v["running"] for v in self.fake.info.values()))
        self.assertEqual(len(self.fake.cold_snapshots), 6)
        for snapshot in self.fake.cold_snapshots:
            self.assertEqual({r for r, running in snapshot.items() if not running},
                             {"postiz", "temporal", "postiz-postgres", "postiz-redis", "temporal-postgresql", "temporal-elasticsearch"})
            self.assertEqual({r for r, running in snapshot.items() if running}, {"temporal-ui", "temporal-admin-tools"})
        self.assertEqual({role for starts, role in self.fake.inspections if starts == 6}, set(b.IDS))
        for role in b.STATELESS:
            self.assertEqual(self.fake.info[role]["started"], self.fake.stateless_started[role])
        self.assertNotIn(PRIVATE, json.dumps(manifest))

    def test_writer_policy_is_exact_and_native_adapter_cannot_operate_stateless_roles(self):
        self.assertEqual(b.START, ("postiz-postgres", "postiz-redis", "temporal-postgresql", "temporal-elasticsearch", "temporal", "postiz"))
        self.assertEqual(set(b.START), {role for role, _ in b.VOLUMES.values()} | {"temporal"})
        self.assertEqual(set(b.STATELESS), {"temporal-ui", "temporal-admin-tools"})
        docker = b.Docker(); commands = []
        docker.docker = lambda *args: commands.append(args)
        for role in b.STATELESS:
            for operation in (docker.start, docker.terminate):
                with self.assertRaises(b.Refusal): operation(role)
        self.assertEqual(commands, [])

    def test_any_active_or_exit_two_writer_refuses_before_first_archive(self):
        for index, (writer, fault) in enumerate((role, fault) for role in b.START for fault in ("active", "exit2")):
            with self.subTest(writer=writer, fault=fault):
                self.fake = Fake()
                self.fake.stamp = lambda index=index: f"20261107T020000.{index:06d}Z"
                def drift(role, writer=writer, fault=fault):
                    if role == b.START[0]:
                        if fault == "active":
                            self.fake.info[writer].update(status="running", running=True)
                        else:
                            self.fake.info[writer]["exit"] = 2
                self.fake.hook = drift
                result = self.execute()
                self.assertEqual(result["status"], "partial-retained")
                self.assertEqual(self.fake.archive_count, 0)
                self.assertTrue(all(info["running"] for info in self.fake.info.values()))
                self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], list(b.START))
                self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())

    def test_stateless_mount_state_start_and_identity_drift_refuse_without_operating_them(self):
        fields = ("mount", "state", "started", "image", "id", "project")
        for index, (role, field) in enumerate((role, field) for role in b.STATELESS for field in fields):
            with self.subTest(role=role, field=field):
                self.fake = Fake()
                self.fake.stamp = lambda index=index: f"20261107T020001.{index:06d}Z"
                def drift(writer, role=role, field=field):
                    if writer != "postiz": return
                    info = self.fake.info[role]
                    if field == "mount": info["mounts"] = [copy.deepcopy(self.fake.info["postiz"]["mounts"][0])]
                    elif field == "state": info.update(status="exited", running=False, exit=2)
                    elif field == "started": info["started"] = "external-restart"
                    elif field == "image": info["image"] = "sha256:" + "e" * 64
                    elif field == "id": info["id"] = "e" * 64
                    else: info["project"] = "foreign"
                self.fake.hook = drift
                result = self.execute()
                self.assertEqual(result["status"], "recovery-required")
                self.assertEqual(self.fake.archive_count, 0)
                self.assertEqual([e for e in self.fake.events if e[0] in ("term", "start")], [("term", "postiz"), ("start", "postiz")])
                self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())

    def test_stateless_restart_during_archiving_prevents_second_archive_and_completion(self):
        archive = self.fake.archive
        def drift(*args):
            archive(*args)
            self.fake.info["temporal-ui"]["started"] = "external-restart"
        self.fake.archive = drift
        result = self.execute()
        self.assertEqual(result["status"], "recovery-required")
        self.assertEqual(self.fake.archive_count, 1)
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], list(b.START))
        self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())

    def test_new_stateless_or_foreign_volume_consumer_refuses_cold_acquisition(self):
        for index, consumer in enumerate([b.IDS[r] for r in b.STATELESS] + ["f" * 64]):
            with self.subTest(consumer_index=index):
                self.fake = Fake()
                self.fake.stamp = lambda index=index: f"20261107T020002.{index:06d}Z"
                def sharing(role, consumer=consumer):
                    if role == "postiz": self.fake.volume_users[next(iter(b.VOLUMES))].append(consumer)
                self.fake.hook = sharing
                result = self.execute()
                self.assertEqual(result["status"], "recovery-required")
                self.assertEqual(self.fake.archive_count, 0)
                self.assertTrue(all(info["running"] for info in self.fake.info.values()))
                self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())

    def test_all_eight_health_checks_refuse_late_stateless_unhealthy_or_restart_drift(self):
        for index, (role, drift) in enumerate((role, drift) for role in b.STATELESS for drift in ("health", "started")):
            with self.subTest(role=role, drift=drift):
                self.fake = Fake()
                self.fake.stamp = lambda index=index: f"20261107T020003.{index:06d}Z"
                start = self.fake.start
                def changed(writer, role=role, drift=drift, start=start):
                    start(writer)
                    if writer == "postiz":
                        self.fake.info[role][drift] = "unhealthy" if drift == "health" else "external-restart"
                self.fake.start = changed
                result = self.execute()
                self.assertEqual(result["status"], "recovery-required")
                self.assertEqual(self.fake.archive_count, 6)
                self.assertEqual({r for count, r in self.fake.inspections if count == 6}, set(b.IDS))
                self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], list(b.START))
                self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())

    def test_root_only(self):
        self.fake.root_uid = 1000
        self.assert_refused()

    def test_wrong_app_container_publisher_image_and_role(self):
        for field, value in [("name", "/postiz-retired"), ("id", "a" * 64), ("image", "sha256:" + "a" * 64),
                             ("project", "retired"), ("service", "unexpected")]:
            with self.subTest(field=field):
                old = self.fake.info["postiz"][field]
                self.fake.info["postiz"][field] = value
                self.assert_refused()
                self.fake.info["postiz"][field] = old

    def test_unexpected_members_and_dependency_reference(self):
        self.fake.member_ids.append("f" * 64)
        self.assert_refused()
        self.fake.member_ids.pop()
        self.fake.info["temporal"]["reference"] = "temporalio/auto-setup:latest"
        self.assert_refused()

    def test_changed_restart_policy_and_submounts_are_refused(self):
        self.fake.info["temporal"]["restart"]["Name"] = "always"
        self.assert_refused()
        self.fake.info["temporal"]["restart"]["Name"] = "unless-stopped"
        self.fake.submounts = lambda: b.need(False)
        self.assert_refused()

    def test_wrong_mount_path_destination_type_and_mapping(self):
        for field, value in [("Source", "/tmp/substitution"), ("Destination", "/other"), ("Type", "bind"),
                             ("Name", "retired_config"), ("RW", False)]:
            old = self.fake.info["postiz"]["mounts"][0][field]
            self.fake.info["postiz"]["mounts"][0][field] = value
            self.assert_refused()
            self.fake.info["postiz"]["mounts"][0][field] = old
        self.fake.info["temporal-ui"]["mounts"] = [copy.deepcopy(self.fake.info["postiz"]["mounts"][0])]
        self.assert_refused()

    def test_wrong_volume_metadata_and_shared_stopped_user(self):
        suffix = next(iter(b.VOLUMES))
        for field, value in [("driver", "remote"), ("plain", False), ("path", "/other")]:
            old = self.fake.volume_info[suffix][field]
            self.fake.volume_info[suffix][field] = value
            self.assert_refused()
            self.fake.volume_info[suffix][field] = old
        self.fake.volume_users[suffix].append("f" * 64)
        self.assert_refused()

    def test_wrong_or_stale_plan_hash_never_stops(self):
        with self.assertRaises(Exception): self.execute("0" * 64)
        approved = self.plan_hash()
        self.fake.info["temporal"]["image"] = "sha256:" + "f" * 64
        with self.assertRaises(Exception): self.execute(approved)
        self.assertEqual(self.fake.events, [])
        self.assertFalse(self.physical(b.BASE).exists())

    def test_unsafe_backup_directory(self):
        self.physical(b.BASE.parent).chmod(0o777)
        self.assert_refused()

    def test_symlink_volume_root_refused(self):
        path = self.physical(b.volume_path(next(iter(b.VOLUMES))))
        path.rename(path.with_name("original"))
        path.symlink_to(path.with_name("original"), target_is_directory=True)
        self.assert_refused()

    def test_volume_inode_substitution_after_approval_refused(self):
        approved = self.plan_hash()
        path = self.physical(b.volume_path(next(iter(b.VOLUMES))))
        path.rename(path.with_name("original")); path.mkdir(mode=0o700)
        with self.assertRaises(Exception): self.execute(approved)
        self.assertEqual(self.fake.events, [])

    def test_incomplete_graceful_stop_recovers_attempted_container(self):
        self.fake.stall.add("postiz")
        result = self.execute()
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(self.fake.archive_count, 0)
        self.assertEqual([e for e in self.fake.events if e[0] == "start"], [("start", "postiz")])
        self.assertLessEqual(self.fake.clock, 45)

    def test_ambiguous_stop_failure_still_restarts(self):
        self.fake.stop_error = "postiz"
        result = self.execute()
        self.assertEqual(result["status"], "partial-retained")
        self.assertTrue(self.fake.info["postiz"]["running"])
        self.assertEqual(self.fake.archive_count, 0)

    def test_unclean_or_oom_stop_cannot_be_called_consistent(self):
        for exit_code, oom in [(137, False), (1, False), (0, True)]:
            with self.subTest(exit_code=exit_code, oom=oom):
                self.fake = Fake()
                self.fake.info["postiz"].update(exit=exit_code, oom=oom)
                self.fake.stamp = lambda exit_code=exit_code, oom=oom: f"20261107T010203.{exit_code + int(oom):06d}Z"
                result = self.execute()
                self.assertEqual(result["status"], "partial-retained")
                self.assertEqual(self.fake.archive_count, 0)
                self.assertTrue(self.fake.info["postiz"]["running"])

    def test_archive_failure_and_interruption_restart_every_original(self):
        for fault in ("failure", "interrupt"):
            with self.subTest(fault=fault):
                self.fake = Fake(); self.fake.archive_error = fault
                # Each case has a different exclusive timestamp, with no deletion/reuse.
                self.fake.stamp = lambda fault=fault: "20261107T01020" + ("4" if fault == "failure" else "5") + ".000004Z"
                result = self.execute()
                self.assertEqual(result["status"], "partial-retained")
                self.assertTrue(all(v["running"] for v in self.fake.info.values()))
                self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], list(b.START))
                directory = self.physical(Path(result["backup_path"]))
                self.assertTrue(directory.exists())
                self.assertFalse((directory / "manifest.json").exists())

    def test_verification_interruption_always_restarts(self):
        with patch.object(b, "verify", side_effect=KeyboardInterrupt(PRIVATE)):
            result = self.execute()
        self.assertEqual(result["status"], "partial-retained")
        self.assertTrue(all(v["running"] for v in self.fake.info.values()))

    def recovery_setup_interrupt(self, boundary="registration", fault="callback", stopped=None):
        """Exact review fault: invoke the acquisition handler during recovery registration."""
        installed = {sig: b.signal.SIG_DFL for sig in b.SIGNALS}
        state = {"injected": False, "mask": set()}

        def inject(point):
            entering = sum(e[0] == "term" for e in self.fake.events) == 6 and not any(e[0] == "start" for e in self.fake.events)
            if point == "after-stop":
                entering = sum(e[0] == "term" for e in self.fake.events) == 1 and not any(e[0] == "start" for e in self.fake.events)
            if point != boundary or not entering:
                return
            if fault == "error":
                state["injected"] = True
                raise OSError(PRIVATE)
            if fault == "interrupt":
                state["injected"] = True
                raise InterruptedError(PRIVATE)
            if not state["injected"]:
                state["injected"] = True
                installed[b.signal.SIGTERM](b.signal.SIGTERM, None)

        def register(sig, handler):
            recovery_handler = handler == b.signal.SIG_IGN or getattr(handler, "__name__", "") == "deferred_interrupt"
            if recovery_handler:
                inject("registration")
            previous = installed[sig]
            installed[sig] = handler
            return previous

        def mask(how, values):
            inject("masking" if how == b.signal.SIG_BLOCK else "unmasking")
            previous = state["mask"].copy()
            if how == b.signal.SIG_BLOCK:
                state["mask"].update(values)
            else:
                self.assertEqual(how, b.signal.SIG_SETMASK)
                state["mask"] = set(values)
            return previous

        def lookup(sig):
            inject("lookup")
            return installed[sig]

        self.fake.signals = lambda recovering=False: b.Docker.signals(self.fake, recovering)
        previous_trace = sys.gettrace()
        def trace(frame, event, arg):
            if event == "call" and frame.f_code is b.Docker.signals.__wrapped__.__code__ and frame.f_locals.get("recovering") is True:
                inject("entry")
            return trace

        if boundary == "after-stop":
            self.fake.hook = lambda role: inject("after-stop")
        if boundary == "after-archive":
            original_archive = self.fake.archive
            def archive(*args):
                original_archive(*args)
                inject("after-archive")
            self.fake.archive = archive
        if boundary == "verification":
            original_now = self.fake.now
            def now():
                if self.fake.archive_count:
                    inject("verification")
                return original_now()
            self.fake.now = now
        with patch.object(b.signal, "signal", side_effect=register), \
                patch.object(b.signal, "getsignal", side_effect=lookup), \
                patch.object(b.signal, "pthread_sigmask", side_effect=mask):
            try:
                if boundary == "entry":
                    sys.settrace(trace)
                result = self.execute()
            finally:
                sys.settrace(previous_trace)
        self.assertTrue(state["injected"], "The handler-setup interruption must actually fire")
        self.assertEqual(state["mask"], set())
        self.assertEqual(installed, {sig: b.signal.SIG_DFL for sig in b.SIGNALS})
        stopped = list(reversed(b.START)) if stopped is None else stopped
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "term"], stopped)
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], [role for role in b.START if role in stopped])
        self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())
        return result

    def test_recovery_handler_setup_interrupt_still_restarts_all_six_writers(self):
        self.fake.archive_error = "interrupt"
        result = self.recovery_setup_interrupt()
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 8)

    def test_recovery_setup_interrupt_and_restart_failure_require_recovery(self):
        self.fake.archive_error = "interrupt"
        self.fake.start_error = b.START[0]
        result = self.recovery_setup_interrupt()
        self.assertEqual(result["status"], "recovery-required")
        self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 7)

    def test_recovery_setup_interrupt_is_not_suppressed_after_healthy_acquisition(self):
        result = self.recovery_setup_interrupt()
        self.assertEqual(self.fake.archive_count, 6)
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 8)

    def test_dispatched_interrupt_at_every_recovery_setup_boundary(self):
        for index, boundary in enumerate(("masking", "lookup", "registration", "unmasking")):
            with self.subTest(boundary=boundary):
                self.fake = Fake(); self.fake.archive_error = "interrupt"
                self.fake.stamp = lambda index=index: f"20261107T010203.{index:06d}Z"
                result = self.recovery_setup_interrupt(boundary)
                self.assertEqual(result["status"], "partial-retained")
                self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 8)

    def test_every_recovery_setup_boundary_retains_failed_restart_classification(self):
        for index, boundary in enumerate(("masking", "lookup", "registration", "unmasking")):
            with self.subTest(boundary=boundary):
                self.fake = Fake(); self.fake.archive_error = "interrupt"; self.fake.start_error = b.START[0]
                self.fake.stamp = lambda index=index: f"20261107T010204.{index:06d}Z"
                result = self.recovery_setup_interrupt(boundary)
                self.assertEqual(result["status"], "recovery-required")
                self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 7)

    def test_setup_api_failures_and_retry_exhaustion_cannot_gate_restart_body(self):
        for index, (boundary, fault) in enumerate((bnd, flt) for bnd in ("masking", "lookup", "registration", "unmasking") for flt in ("error", "interrupt")):
            with self.subTest(boundary=boundary, fault=fault):
                self.fake = Fake()
                self.fake.stamp = lambda index=index: f"20261107T010205.{index:06d}Z"
                result = self.recovery_setup_interrupt(boundary, fault)
                self.assertEqual(result["status"], "partial-retained")
                self.assertEqual(self.fake.archive_count, 6)
                self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 8)

    def test_exact_generator_entry_callback_restarts_every_original(self):
        self.fake.archive_error = "interrupt"
        result = self.recovery_setup_interrupt("entry")
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 8)

    def test_generator_entry_callback_continues_after_failed_restart(self):
        self.fake.archive_error = "interrupt"; self.fake.start_error = b.START[0]
        result = self.recovery_setup_interrupt("entry")
        self.assertEqual(result["status"], "recovery-required")
        self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 7)

    def test_generator_entry_cancellation_never_commits_healthy_archives(self):
        result = self.recovery_setup_interrupt("entry")
        self.assertEqual(self.fake.archive_count, 6)
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(sum(v["running"] for v in self.fake.info.values()), 8)

    def test_actual_signal_callbacks_only_record_pending_and_checkpoint_raises(self):
        installed = {sig: b.signal.SIG_DFL for sig in b.SIGNALS}
        masks = set()
        def register(sig, handler):
            old = installed[sig]; installed[sig] = handler; return old
        def mask(how, values):
            nonlocal masks
            old = masks.copy()
            masks = masks | set(values) if how == b.signal.SIG_BLOCK else set(values)
            return old
        with patch.object(b.signal, "signal", side_effect=register), \
                patch.object(b.signal, "getsignal", side_effect=lambda sig: installed[sig]), \
                patch.object(b.signal, "pthread_sigmask", side_effect=mask):
            with b.Docker.signals(self.fake):
                for sig in b.SIGNALS:
                    self.assertIsNone(installed[sig](sig, None))
                    self.assertTrue(self.fake._signal_state["pending"])
                with self.assertRaises(InterruptedError): b.acquisition_checkpoint(self.fake)
                with b.Docker.signals(self.fake, recovering=True):
                    for sig in b.SIGNALS:
                        self.assertIsNone(installed[sig](sig, None))
                # Recovery completed despite pending cancellation; it is raised
                # only at an explicit checkpoint outside the recovery context.
                with self.assertRaises(InterruptedError): b.acquisition_checkpoint(self.fake)
        self.assertEqual(masks, set())
        self.assertEqual(installed, {sig: b.signal.SIG_DFL for sig in b.SIGNALS})

    def test_pending_signal_finishes_bounded_stop_then_recovers_prefix(self):
        self.fake.stall.add("postiz")
        inspect = self.fake.inspect
        def stopped_after_two_ticks(role):
            if role == "postiz" and self.fake.clock >= 2:
                self.fake.info[role].update(status="exited", running=False)
                self.fake.stall.clear()
            return inspect(role)
        self.fake.inspect = stopped_after_two_ticks
        # Stop reporting the old exit state after the matching start.
        start = self.fake.start
        def restart(role):
            self.fake.inspect = inspect
            start(role)
        self.fake.start = restart
        result = self.recovery_setup_interrupt("after-stop", stopped=["postiz"])
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(self.fake.clock, 2)
        self.assertEqual(self.fake.archive_count, 0)
        self.assertTrue(all(info["running"] for info in self.fake.info.values()))

    def test_pending_signal_after_archive_stops_further_copying_and_recovers(self):
        result = self.recovery_setup_interrupt("after-archive")
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(self.fake.archive_count, 1)
        self.assertTrue(all(info["running"] for info in self.fake.info.values()))

    def test_pending_signal_during_verification_recovers_without_completion(self):
        result = self.recovery_setup_interrupt("verification")
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(self.fake.archive_count, 1)
        self.assertTrue(all(info["running"] for info in self.fake.info.values()))

    def test_restart_failure_does_not_skip_other_originals(self):
        self.fake.start_error = "temporal-postgresql"
        result = self.execute()
        self.assertEqual(result["status"], "recovery-required")
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "start"], list(b.START))
        self.assertFalse((self.physical(Path(result["backup_path"])) / "manifest.json").exists())

    def test_corrupt_archive_and_unsafe_members_refused_without_extracting(self):
        for index, kind in enumerate(("corrupt", "absolute", "traversal", "symlink", "hardlink", "special")):
            with self.subTest(kind=kind):
                self.fake = Fake(); self.fake.archive_kind = kind
                self.fake.stamp = lambda index=index: f"20261107T0102{index:02d}.000004Z"
                result = self.execute()
                self.assertEqual(result["status"], "partial-retained")
                self.assertTrue(all(v["running"] for v in self.fake.info.values()))

    def test_checksum_mismatch_refused_after_valid_acquisition(self):
        result = self.execute()
        directory = self.physical(Path(result["backup_path"]))
        entries = json.loads((directory / "manifest.json").read_text())["artifacts"]
        entries[0]["sha256"] = "0" * 64
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(Exception): b.verify(fd, entries, self.fake, self.fake.now() + 100)
        finally: os.close(fd)

    def test_archive_bytes_changed_after_hashing_are_refused(self):
        result = self.execute()
        directory = self.physical(Path(result["backup_path"]))
        entries = json.loads((directory / "manifest.json").read_text())["artifacts"]
        path = directory / entries[0]["file"]
        with path.open("r+b") as handle:
            # A payload byte changes without changing framing or size.
            handle.seek(512); handle.write(b"X")
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(Exception): b.verify(fd, entries, self.fake, self.fake.now() + 100)
        finally: os.close(fd)

    def test_trailing_junk_and_truncation_refused(self):
        result = self.execute()
        directory = self.physical(Path(result["backup_path"]))
        entries = json.loads((directory / "manifest.json").read_text())["artifacts"]
        path = directory / entries[0]["file"]
        original = path.read_bytes()
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            # Removing redundant zero padding remains a valid tar, but cannot
            # match the previously captured byte count/checksum.
            path.write_bytes(original[:-512])
            self.assertEqual(b.check_archive(fd, path.name, self.fake, self.fake.now() + 100)["bytes"], len(original) - 512)
            with self.assertRaises(Exception): b.verify(fd, entries, self.fake, self.fake.now() + 100)
            for content in (original + b"X" * 512 + b"\0" * 1024, original[:1024], original[:1536]):
                path.write_bytes(content)
                with self.assertRaises(Exception): b.check_archive(fd, path.name, self.fake, self.fake.now() + 100)
        finally: os.close(fd)

    def test_path_substitution_while_stopping_refuses_before_archive_and_recovers(self):
        def replace_path(role):
            if role == "postiz":
                base = self.physical(b.BASE)
                base.rename(base.with_name("moved-current-app"))
                base.mkdir(mode=0o700)
        self.fake.hook = replace_path
        result = self.execute()
        self.assertEqual(result["status"], "partial-retained")
        self.assertEqual(self.fake.archive_count, 0)
        self.assertTrue(all(v["running"] for v in self.fake.info.values()))

    def test_exclusive_backup_collision_never_reuses_or_deletes_files(self):
        first = self.execute()
        directory = self.physical(Path(first["backup_path"]))
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        events = self.fake.events[:]
        second = self.execute()
        self.assertEqual(second["status"], "partial-retained")
        self.assertEqual(self.fake.events, events)
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})

    def test_existing_backup_lock_refuses_without_signals(self):
        base = self.physical(b.BASE); base.mkdir(mode=0o700)
        fd = os.open(base / ".backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute()
            self.assertEqual(result["status"], "partial-retained")
            self.assertEqual(self.fake.events, [])
        finally: os.close(fd)

    def test_native_adapter_command_shapes_are_bounded_without_executing(self):
        docker = b.Docker()
        calls = []
        docker.docker = lambda *args: calls.append(args)
        docker.terminate("postiz"); docker.start("postiz")
        self.assertEqual(calls, [("container", "kill", "--signal=SIGTERM", b.IDS["postiz"]),
                                 ("container", "start", b.IDS["postiz"])])
        self.assertNotIn("SIGKILL", str(calls))

    def test_native_consumer_projection_detects_bind_aliases_and_parent_binds(self):
        suffix = next(iter(b.VOLUMES)); root = str(b.volume_path(suffix))
        for source in (root, root + "/child", str(Path(root).parent), "/"):
            with self.subTest(source=source):
                docker = b.Docker(); calls = []
                foreign = "f" * 64
                def metadata(*args):
                    calls.append(args)
                    if args[:2] == ("container", "ls"):
                        return (foreign + "\n").encode()
                    self.assertEqual(args[:3], ("container", "inspect", "--format"))
                    self.assertNotIn(".Config", args[3])
                    return json.dumps({"id": foreign, "mounts": [{"Type": "bind", "Source": source}]}).encode()
                docker.docker = metadata
                self.assertEqual(docker.consumers()[suffix], [foreign])
                self.assertEqual(len(calls), 2)

    def test_native_consumer_projection_ignores_unrelated_and_tmpfs_mounts(self):
        docker = b.Docker(); foreign = "f" * 64
        def metadata(*args):
            if args[:2] == ("container", "ls"): return (foreign + "\n").encode()
            self.assertEqual(args[:3], ("container", "inspect", "--format"))
            return json.dumps({"id": foreign, "mounts": [{"Type": "tmpfs", "Source": ""},
                                                           {"Type": "bind", "Source": "/unrelated"}]}).encode()
        docker.docker = metadata
        self.assertTrue(all(not users for users in docker.consumers().values()))

    def test_cli_failure_is_compact_and_does_not_echo_private_exceptions(self):
        self.fake.root_uid = 1000
        output = io.StringIO()
        with patch.object(b, "Docker", return_value=self.fake), patch.object(sys, "argv", ["backup-current.py"]), contextlib.redirect_stdout(output):
            self.assertEqual(b.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "refused", "backup_path": None})
        self.assertNotIn(PRIVATE, output.getvalue())


class StrictHealthTemplate(unittest.TestCase):
    """Use installed Go text/template, not a substitute template implementation."""
    @classmethod
    def setUpClass(cls):
        go = shutil.which("go")
        if go is None:
            raise RuntimeError("Installed Go is required for strict Docker-template regression proof")
        cls.temporary = tempfile.TemporaryDirectory(prefix="arkx-template-offline-")
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name).resolve()
        cls.environment = {"HOME": str(root), "TMPDIR": str(root), "PATH": "/usr/bin:/bin",
                           "GOCACHE": str(root / "cache"), "GOPATH": str(root / "gopath"),
                           "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local",
                           "GOENV": "off", "GOWORK": "off", "GO111MODULE": "off",
                           "GOTELEMETRY": "off", "CGO_ENABLED": "0"}
        source = root / "strict.go"
        source.write_text('''package main
import ("encoding/json"; "os"; "text/template")
func main() {
 var input struct { Template string; Value map[string]interface{} }
 if json.NewDecoder(os.Stdin).Decode(&input) != nil { os.Exit(2) }
 funcs := template.FuncMap{"json": func(v interface{}) (string, error) {
  bytes, err := json.Marshal(v); return string(bytes), err
 }}
 tpl, err := template.New("inspect").Option("missingkey=error").Funcs(funcs).Parse(input.Template)
 if err == nil { err = tpl.Execute(os.Stdout, input.Value) }
 if err != nil { os.Stderr.WriteString(err.Error()); os.Exit(1) }
}
''')
        cls.renderer = root / "strict-template"
        subprocess.run([go, "build", "-o", str(cls.renderer), str(source)], cwd=root,
                       env=cls.environment, check=True, capture_output=True, timeout=120)

    def render(self, template, metadata):
        return subprocess.run([str(self.renderer)], input=json.dumps({"Template": template, "Value": metadata}).encode(),
                              env=self.environment, capture_output=True, timeout=10)

    def projection(self, metadata):
        adapter = b.Docker()
        def fake_docker(args, **options):
            self.assertEqual(args[:9], ["/usr/bin/docker", "--config", "/nonexistent/arkx-current-backup",
                                      "--host", "unix:///var/run/docker.sock", "container", "inspect", "--format", args[8]])
            self.assertEqual(len(args), 10)
            self.assertEqual(args[9], b.IDS["postiz"])
            self.assertEqual(options["env"], {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/nonexistent"})
            self.assertIs(options["stderr"], subprocess.DEVNULL)
            rendered = self.render(args[8], metadata)
            if rendered.returncode:
                raise subprocess.CalledProcessError(rendered.returncode, args)
            return subprocess.CompletedProcess(args, 0, rendered.stdout)
        # The real subprocess runner is retained only for the local stdlib Go
        # renderer; every constructed Docker call is intercepted here.
        native_render = self.render
        real_run = subprocess.run
        def render_without_transport_patch(template, value):
            return real_run([str(self.renderer)], input=json.dumps({"Template": template, "Value": value}).encode(),
                            env=self.environment, capture_output=True, timeout=10)
        self.render = render_without_transport_patch
        try:
            with patch.object(b.subprocess, "run", side_effect=fake_docker):
                return adapter.inspect("postiz")
        finally:
            self.render = native_render

    def metadata(self):
        info = Fake().info["postiz"]
        return {"Id": info["id"], "Name": info["name"], "Image": info["image"],
                "Config": {"Image": info["reference"], "Labels": {"com.docker.compose.project": b.PROJECT, "com.docker.compose.service": "postiz"}},
                "HostConfig": {"RestartPolicy": info["restart"]}, "Mounts": info["mounts"],
                "State": {"StartedAt": info["started"], "Status": "running", "Running": True, "ExitCode": 0, "OOMKilled": False}}

    def test_actual_command_template_accepts_missing_health_and_retains_health_states(self):
        for health in (None, "healthy", "unhealthy", "starting"):
            with self.subTest(health=health):
                metadata = self.metadata()
                if health is not None:
                    metadata["State"]["Health"] = {"Status": health}
                info = self.projection(metadata)
                self.assertEqual(info["health"], health or "")
                b.identity(info, "postiz")
                clock = Fake()
                clock.inspect = lambda role: copy.deepcopy(info)
                pinned = b.identity(info, "postiz")
                if health in ("unhealthy", "starting"):
                    with self.assertRaises(b.Refusal): b.wait(clock, "postiz", True, 2, pinned)
                else:
                    b.wait(clock, "postiz", True, 2, pinned)
                info.update(status="exited", running=False)
                with self.assertRaises(b.Refusal): b.wait(clock, "postiz", True, clock.now() + 2, pinned)

    def test_strict_engine_reproduces_old_missing_health_failure(self):
        template = '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}""{{end}}'
        result = self.render(template, self.metadata())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'map has no entry for key "Health"', result.stderr)


if __name__ == "__main__":
    unittest.main()
