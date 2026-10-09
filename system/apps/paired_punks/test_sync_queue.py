"""Syncing while a partner syncs: both land, nobody sees an error."""

import subprocess
import threading
from pathlib import Path

import pytest
from paired_punks import projects
from paired_punks.github import GitHubError


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _clone(remote: Path, where: Path, login: str) -> Path:
    _run("clone", "-q", str(remote), str(where))
    _run("config", "user.name", login, cwd=where)
    _run("config", "user.email", f"{login}@example.com", cwd=where)
    return where


@pytest.fixture
def shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(projects, "PARTNER_POLL_SECONDS", 0.05)
    remote = tmp_path / "remote.git"
    _run("init", "-q", "--bare", "-b", "main", str(remote))
    seed = _clone(remote, tmp_path / "seed", "seed")
    (seed / "README.md").write_text("project\n")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "start", cwd=seed)
    _run("push", "-q", "origin", "HEAD:main", cwd=seed)
    return remote


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    _run("add", "-A", cwd=repo)
    _run("commit", "-q", "-m", name, cwd=repo)


def test_partners_syncing_at_once_both_land(shared: Path, tmp_path: Path) -> None:
    people = [_clone(shared, tmp_path / f"p{i}", f"p{i}") for i in range(3)]
    for round_number in range(3):
        errors: list[Exception] = []

        def sync(repo: Path, name: str) -> None:
            _commit(repo, name)
            try:
                projects._exchange(repo, str(shared))
            except GitHubError as error:
                errors.append(error)

        threads = [threading.Thread(target=sync, args=(repo, f"{repo.name}-{round_number}.txt")) for repo in people]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []

    files = set(_run("ls-tree", "--name-only", "main", cwd=shared).split())
    assert {f"p{i}-{r}.txt" for i in range(3) for r in range(3)} <= files


def test_a_push_failure_with_no_partner_still_reports(shared: Path, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _clone(shared, tmp_path / "solo", "solo")
    _commit(repo, "mine.txt")
    hook = shared / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'refusing everything' >&2\nexit 1\n")
    hook.chmod(0o755)
    waits: list[int] = []
    monkeypatch.setattr(projects, "_wait_for_partner", lambda url, round_number: waits.append(round_number))
    with pytest.raises(GitHubError):
        projects._exchange(repo, str(shared))
    assert waits == []  # the shared copy never moved, so it didn't wait for a partner
