from pathlib import Path

from engineering.git_service import GitService
from engineering.models import RepositoryState


def test_repository_state_matches_current_repo() -> None:
    repo_root = Path.cwd()
    state = GitService(repo_root).repository_state()

    assert isinstance(state, RepositoryState)
    assert state.root == repo_root
    assert state.branch == "agent/ops-autonomous-workflow-v1"
    assert state.is_clean is False
