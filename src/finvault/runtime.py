"""Identify the running source tree, independent of the deployment symlink."""

import re
import subprocess
from pathlib import Path


class RuntimeInfo:
    def __init__(self, root: Path):
        # Resolve once: 'current' can point to a different release after startup.
        self.root = root.resolve()
        self.commit_id = None
        if self.root.parent.name == "releases" and self.valid_commit(self.root.name):
            self.commit_id = self.root.name
        elif (self.root / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True, timeout=2,
                )
                commit = result.stdout.strip()
                if self.valid_commit(commit):
                    self.commit_id = commit
            except (OSError, subprocess.SubprocessError):
                pass

    @staticmethod
    def valid_commit(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None

