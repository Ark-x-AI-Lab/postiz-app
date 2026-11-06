"""Offline regression checks for public dependency-cache configuration."""
import importlib.util
from pathlib import Path
import unittest
import contextlib
import io
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("arkx_preflight", Path(__file__).with_name("preflight.py"))
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
PUBLIC = """ignore-workspace-root-check=true
node-linker=hoisted
restrict-manifest-changes=false
sync-injected-deps-after-scripts[]=build
inject-workspace-packages=true
"""

class PublicBuildInputs(unittest.TestCase):
    def test_allowed_public_settings(self):
        for value in (PUBLIC, "\n\n" + PUBLIC,
                      "\n".join(reversed(PUBLIC.splitlines())),
                      PUBLIC.replace("ignore-workspace-root-check=true", "ignore-workspace-root-check = false")):
            with self.subTest(value=value):
                preflight.validate_public_pnpm_config(value)

    def test_configuration_file_boundary(self):
        with tempfile.TemporaryDirectory(prefix="arkx-public-config-test-") as directory:
            root = Path(directory)
            config = root / ".npmrc"
            with patch.object(preflight, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "regular file"):
                    preflight.public_build_input_checks()
                config.write_text(PUBLIC)
                with contextlib.redirect_stdout(io.StringIO()):
                    preflight.public_build_input_checks()
                config.unlink()
                target = root / "synthetic-config"
                target.write_text(PUBLIC)
                config.symlink_to(target)
                with self.assertRaisesRegex(ValueError, "regular file"):
                    preflight.public_build_input_checks()
                config.unlink()
                config.write_text("#" * 8193)
                with self.assertRaisesRegex(ValueError, "too large"):
                    preflight.public_build_input_checks()

    def test_rejects_unreviewed_inputs_without_echoing_values(self):
        private = "SYNTHETIC_PRIVATE_VALUE"
        rejected = {
            "authentication": PUBLIC + "//registry.invalid/:_authToken=" + private,
            "hash comment": PUBLIC + "# " + private,
            "semicolon comment": PUBLIC + "; " + private,
            "registry": PUBLIC + "registry=https://registry.invalid/" + private,
            "environment expansion": PUBLIC.replace("node-linker=hoisted", "node-linker=${" + private + "}"),
            "duplicate": PUBLIC + "node-linker=hoisted",
            "missing": PUBLIC.replace("inject-workspace-packages=true\n", ""),
            "different layout": PUBLIC.replace("hoisted", "isolated"),
            "lock disagreement": PUBLIC.replace("inject-workspace-packages=true", "inject-workspace-packages=false"),
            "unreviewed script": PUBLIC.replace("[]=build", "[]=" + private),
            "unknown key": PUBLIC + "unreviewed-setting=" + private,
            "invalid line": PUBLIC + private,
            "oversized": PUBLIC + "#" + private * 1000,
        }
        for name, value in rejected.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as caught:
                    preflight.validate_public_pnpm_config(value)
                self.assertNotIn(private, str(caught.exception))

if __name__ == "__main__":
    unittest.main()
