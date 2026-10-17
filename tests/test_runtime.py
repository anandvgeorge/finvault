"""Runtime metadata checks use only temporary releases and fake Git output."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from finvault.runtime import RuntimeInfo


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.commit = "a" * 40
        self.release = self.root / "releases" / self.commit
        self.release.mkdir(parents=True)

    def test_running_release_stays_pinned_when_current_symlink_changes(self):
        current = self.root / "current"
        current.symlink_to(self.release)
        runtime = RuntimeInfo(current)
        self.assertEqual(runtime.commit_id, self.commit)
        current.unlink()
        current.symlink_to(self.root)
        self.assertEqual(runtime.commit_id, self.commit)

    def test_local_commit_is_captured_once_at_startup_and_has_no_deployment(self):
        (self.root / ".git").mkdir()
        with patch("finvault.runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0, self.commit + "\n")) as git:
            runtime = RuntimeInfo(self.root)
            self.assertEqual(runtime.commit_id, self.commit)
            git.assert_called_once()

    def test_missing_git_or_source_metadata_returns_unknown(self):
        self.assertIsNone(RuntimeInfo(self.root).commit_id)
        (self.root / ".git").mkdir()
        with patch("finvault.runtime.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(RuntimeInfo(self.root).commit_id)
