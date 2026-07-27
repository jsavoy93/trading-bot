from __future__ import annotations

import subprocess
from pathlib import Path
from engineering.models import RepositoryState


class GitService:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def current_branch(self) -> str:
        return self.run("branch", "--show-current")

    def is_clean(self) -> bool:
        return self.run("status", "--porcelain") == ""

    def repository_state(self) -> RepositoryState:
        return RepositoryState(
            root=self.repo_root,
            branch=self.current_branch(),
            is_clean=self.is_clean(),
        )
