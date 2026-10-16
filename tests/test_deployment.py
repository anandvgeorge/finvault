"""Exercise deployment failures without contacting GitHub, Gmail, or systemd."""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-pi.sh"


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="finvault-deployment-")
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.repo = self.home / "finvault"
        self.remote = self.home / "origin.git"
        self.state = self.home / ".local/share/finvault"
        self.state.mkdir(parents=True)
        self.bin = self.home / ".local/bin"
        self.bin.mkdir(parents=True)
        self.events = self.home / "events"
        self.timer = self.home / "timer-active"
        self.timer.touch()
        self.current = self.state / "current"
        self.current.symlink_to(self.repo)
        self.environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        self.git("init", "--bare", str(self.remote), cwd=self.home)
        self.git("init", "-b", "main", str(self.repo), cwd=self.home)
        self.git("config", "user.name", "Deployment Test")
        self.git("config", "user.email", "deployment@example.test")
        self.git("remote", "add", "origin", str(self.remote))
        (self.repo / "release.txt").write_text("Synthetic release\n")
        self.git("add", "release.txt")
        self.git("commit", "-m", "Synthetic release")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("push", "origin", "HEAD:refs/heads/production")

        # Keep sentinels separate from all source and release directories.
        self.sentinels = {}
        for name in ("credentials.json", "token.json", "data/finvault.db"):
            path = self.state / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic runtime sentinel\n")
            self.sentinels[path] = path.read_bytes()

        self.executable("systemctl", r'''
import os
from pathlib import Path
import sys

home = Path.home()
state = home / ".local/share/finvault"
timer = home / "timer-active"
args = sys.argv[1:]
args.remove("--user")
operation = args[0]
unit = args[-1]
if operation == "show":
    if "--property=WorkingDirectory" in args:
        print(state)
    elif "--property=ExecStart" in args:
        start = home / "finvault" if os.getenv("FAKE_OLD_UNIT") else state / "current"
        print(f"path={start}/.venv/bin/python")
    elif "--property=ActiveState" in args:
        busy = home / "ingest-busy"
        if busy.exists():
            busy.unlink()
            print("activating")
        else:
            print("inactive")
elif operation == "is-active":
    sys.exit(0 if timer.exists() else 3)
elif operation in ("stop", "start", "restart"):
    with (home / "events").open("a") as file:
        file.write(f"{operation} {unit}\n")
    if operation == "stop":
        timer.unlink()
    elif operation == "start":
        timer.touch()
    elif operation == "restart" and os.getenv("FAKE_RESTART_FAIL"):
        if (state / "current").resolve().name == os.environ["FAKE_RESTART_FAIL"]:
            sys.exit(1)
else:
    raise SystemExit(f"Unexpected systemctl call: {args}")
''')
        self.executable("uv", r'''
import os
from pathlib import Path
import sys

if os.getenv("FAKE_UV_FAIL"):
    raise SystemExit(1)
args = sys.argv[1:]
assert args[:3] == ["sync", "--locked", "--project"]
assert "VIRTUAL_ENV" not in os.environ
assert "UV_PROJECT_ENVIRONMENT" not in os.environ
release = Path(args[3])
python = release / ".venv/bin/python"
python.parent.mkdir(parents=True, exist_ok=True)
python.write_text("#!" + sys.executable + "\n" + (
    "import os, pathlib, sys\n"
    "release = pathlib.Path(__file__).resolve().parents[2]\n"
    "if sys.argv[1] == '-' and release.name == os.getenv('FAKE_UNHEALTHY_COMMIT'):\n"
    "    raise SystemExit(1)\n"
))
python.chmod(0o700)
''')
        # The initial installation must also support the mocked rollback probe.
        old_python = self.repo / ".venv/bin/python"
        old_python.parent.mkdir(parents=True)
        old_python.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
        old_python.chmod(0o700)

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o700)

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.repo, env=self.environment,
            check=True, capture_output=True, text=True,
        )

    def deploy(self, **overrides):
        return subprocess.run(
            ["bash", str(SCRIPT)], env={**self.environment, **overrides},
            capture_output=True, text=True, timeout=15,
        )

    def assert_runtime_untouched(self):
        for path, contents in self.sentinels.items():
            self.assertEqual(path.read_bytes(), contents)

    def event_lines(self):
        return self.events.read_text().splitlines() if self.events.exists() else []

    def test_success_deploys_exact_commit_and_preserves_runtime(self):
        result = self.deploy(VIRTUAL_ENV="ignored", UV_PROJECT_ENVIRONMENT="ignored")
        self.assertEqual(result.returncode, 0, result.stderr)
        release = self.state / "releases" / self.commit
        self.assertEqual(self.current.resolve(), release)
        self.assertEqual((release / "release.txt").read_text(), "Synthetic release\n")
        self.assertEqual(self.event_lines(), [
            "stop finvault-ingest.timer", "restart finvault-ui.service",
            "start finvault-ingest.timer",
        ])
        self.assertTrue(self.timer.exists())
        self.assert_runtime_untouched()

    def test_unchanged_commit_does_not_build_or_restart(self):
        self.assertEqual(self.deploy().returncode, 0)
        self.events.unlink()
        result = self.deploy(FAKE_UV_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already running", result.stdout)
        self.assertEqual(self.event_lines(), [])

    def test_failed_install_leaves_running_release_and_timer_untouched(self):
        result = self.deploy(FAKE_UV_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.current.resolve(), self.repo)
        self.assertTrue(self.timer.exists())
        self.assertEqual(self.event_lines(), [])
        self.assert_runtime_untouched()

    def test_unhealthy_release_rolls_back_and_resumes_timer(self):
        result = self.deploy(FAKE_UNHEALTHY_COMMIT=self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Previous release restored", result.stdout)
        self.assertEqual(self.current.resolve(), self.repo)
        self.assertEqual(self.event_lines().count("restart finvault-ui.service"), 2)
        self.assertTrue(self.timer.exists())
        self.assert_runtime_untouched()

    def test_failed_restart_rolls_back_and_resumes_timer(self):
        result = self.deploy(FAKE_RESTART_FAIL=self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.current.resolve(), self.repo)
        self.assertTrue(self.timer.exists())
        self.assertIn("Previous release restored", result.stdout)

    def test_disabled_ingestion_timer_stays_disabled(self):
        self.timer.unlink()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.timer.exists())
        self.assertEqual(self.event_lines(), ["restart finvault-ui.service"])

    def test_old_unit_paths_are_rejected_before_any_service_changes(self):
        result = self.deploy(FAKE_OLD_UNIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must start Python through", result.stderr)
        self.assertEqual(self.current.resolve(), self.repo)
        self.assertEqual(self.event_lines(), [])

    def test_concurrent_deployment_is_skipped(self):
        with (self.state / "deploy.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Another deployment is running", result.stdout)
        self.assertEqual(self.event_lines(), [])

    def test_activating_ingestion_is_allowed_to_finish(self):
        (self.home / "ingest-busy").touch()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "ingest-busy").exists())
        self.assertTrue(self.timer.exists())


if __name__ == "__main__":
    unittest.main()
