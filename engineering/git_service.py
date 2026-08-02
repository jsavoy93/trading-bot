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

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=self.repo_root,
            check=False,
        )
        return result.returncode == 0

    def prepare_feature_branch(
        self,
        branch: str,
        expected_source_branch: str,
    ) -> RepositoryState:
        if not self.repo_root.is_dir() or not (self.repo_root / ".git").exists():
            raise RuntimeError(
                f"Repository does not exist at expected path: {self.repo_root}"
            )

        state = self.repository_state()

        if not state.is_clean:
            raise RuntimeError(
                "Refusing to prepare branch because the repository is not clean."
            )

        if not self.branch_exists(expected_source_branch):
            raise RuntimeError(
                "Refusing to prepare branch because the expected source branch "
                f"{expected_source_branch!r} does not exist."
            )

        feature_exists = self.branch_exists(branch)

        if state.branch == branch:
            if not self.is_ancestor(expected_source_branch, branch):
                raise RuntimeError(
                    f"Refusing to resume {branch!r} because it is not based on "
                    f"{expected_source_branch!r}."
                )
            return state

        if state.branch != expected_source_branch:
            raise RuntimeError(
                "Refusing to prepare branch because the current branch "
                f"is {state.branch!r}, expected {expected_source_branch!r} "
                f"or {branch!r}."
            )

        if feature_exists:
            if not self.is_ancestor(expected_source_branch, branch):
                raise RuntimeError(
                    f"Refusing to resume {branch!r} because it is not based on "
                    f"{expected_source_branch!r}."
                )
            self.run("switch", branch)
        else:
            self.run("switch", "-c", branch)

        prepared_state = self.repository_state()

        if prepared_state.branch != branch or not prepared_state.is_clean:
            raise RuntimeError(
                f"Branch preparation did not produce clean branch {branch!r}."
            )

        return prepared_state

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
