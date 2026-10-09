"""What a partner puts in a shared project can't reach outside it on this machine."""

import json
import re
import subprocess
from pathlib import Path

import pytest
from paired_punks import projects
from paired_punks.github import GitHubError

APP_HTML = Path(__file__).parent / "src" / "paired_punks" / "app.html"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> projects.Store:
    store = projects.Store(tmp_path / "data")
    store._me = {"login": "ava", "id": 1, "name": "Ava", "avatar_url": None}
    repo = store.repo_dir("plant-pal")
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    store._save_project({"slug": "plant-pal", "repo": "ava/pp-plant-pal", "name": "Plant Pal", "event": "", "last_sync": ""})
    return store


def test_a_planted_folder_link_is_not_written_through(store: projects.Store, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    files = store.repo_dir("plant-pal") / "files"
    files.mkdir()
    (files / "ava").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(GitHubError):
        store.add_file("plant-pal", "SKILL.md", b"overwritten")
    assert list(elsewhere.iterdir()) == []


def test_a_planted_file_link_is_replaced_not_followed(store: projects.Store, tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    mine = store.repo_dir("plant-pal") / "files" / "ava"
    mine.mkdir(parents=True)
    (mine / "notes.txt").symlink_to(victim)
    store.add_file("plant-pal", "notes.txt", b"shared")
    assert victim.read_text() == "keep me"
    assert (mine / "notes.txt").read_text() == "shared"
    assert not (mine / "notes.txt").is_symlink()


def test_a_planted_chat_link_is_not_written_through(store: projects.Store, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("keep me")
    chats = store.repo_dir("plant-pal") / "chats" / "ava"
    chats.mkdir(parents=True)
    (chats / "c1.json").symlink_to(victim)
    monkeypatch.setattr(projects.transcripts, "export_chat", lambda chat_id: {
        "id": chat_id, "title": "t", "last_activity": "", "turns": [], "artifact_paths": [], "raw_lines": []})
    store._export_chat("plant-pal", "c1")
    assert victim.read_text() == "keep me"
    assert json.loads((chats / "c1.json").read_text())["id"] == "c1"


def test_private_workspace_state_is_never_shared(store: projects.Store) -> None:
    secrets = Path("/home/user/workspace/data/.secrets")
    with pytest.raises(GitHubError):
        store.add_workspace_path("plant-pal", secrets)


def test_only_known_projects_are_served(store: projects.Store) -> None:
    with pytest.raises(GitHubError):
        store.safe_path("..", "projects.json")


@pytest.mark.frontend
def test_inline_handlers_get_values_as_js_strings() -> None:
    """attr() alone in an inline handler lets a partner's file name break out of the quotes: the browser
    turns &#39; back into a quote before the handler runs."""
    html = APP_HTML.read_text()
    assert re.findall(r"'\$\{(?:attr|esc)\(", html) == []
    assert "const js = (s) => attr(JSON.stringify(" in html
