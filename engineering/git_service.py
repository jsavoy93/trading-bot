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

    def branch_exists(self, branch: str) -> bool:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.repo_root,
            check=False,
        )
        return result.returncode == 0

    def create_and_checkout_branch(
        self,
        branch: str,
        expected_source_branch: str,
    ) -> RepositoryState:
        state = self.repository_state()

        if not state.is_clean:
            raise RuntimeError(
                "Refusing to create branch because the repository is not clean."
            )

        if state.branch != expected_source_branch:
            raise RuntimeError(
                "Refusing to create branch because the current branch "
                f"is {state.branch!r}, expected {expected_source_branch!r}."
            )

        if self.branch_exists(branch):
            raise RuntimeError(
                f"Refusing to create branch because {branch!r} already exists."
            )

        self.run("switch", "-c", branch)

        new_state = self.repository_state()

        if new_state.branch != branch:
            raise RuntimeError(
                f"Branch creation did not switch to expected branch {branch!r}."
            )

        return new_state
