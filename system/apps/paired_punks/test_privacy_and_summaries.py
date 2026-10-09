"""The GitHub privacy check and the catch-up summary cache."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from paired_punks import projects, summarize
from paired_punks.github import GitHubError


@pytest.fixture
def store(tmp_path: Path) -> projects.Store:
    store = projects.Store(tmp_path / "data")
    store._me = {"login": "ava", "id": 1, "name": "Ava", "avatar_url": None}
    repo = store.repo_dir("plant-pal")
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    store._save_project({"slug": "plant-pal", "repo": "ava/pp-plant-pal", "name": "Plant Pal", "event": "", "last_sync": ""})
    return store


def test_a_public_project_is_recorded_as_public(store: projects.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projects, "api", lambda method, path, body=None: {"private": False, "permissions": {"admin": True}})
    project = store.check_privacy("plant-pal")
    assert project["private"] is False and project["can_change_visibility"] is True
    assert store.project("plant-pal")["private"] is False


def test_making_it_private_asks_github_then_rechecks(store: projects.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_api(method: str, path: str, body: Any = None) -> Any:
        calls.append((method, path, body))
        return {"private": True, "permissions": {"admin": True}}

    monkeypatch.setattr(projects, "api", fake_api)
    assert store.make_private("plant-pal")["private"] is True
    assert calls[0] == ("PATCH", "/repos/ava/pp-plant-pal", {"private": True})


def test_a_partner_is_told_only_the_owner_can_make_it_private(store: projects.Store,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(method: str, path: str, body: Any = None) -> Any:
        raise GitHubError("GitHub said: Must have admin rights to Repository. (403)")

    monkeypatch.setattr(projects, "api", refuse)
    with pytest.raises(GitHubError, match="Only ava, who started this project"):
        store.make_private("plant-pal")


def test_a_failed_privacy_check_does_not_fail_the_sync(store: projects.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(method: str, path: str, body: Any = None) -> Any:
        raise GitHubError("Could not reach GitHub.")

    monkeypatch.setattr(projects, "api", unreachable)
    monkeypatch.setattr(projects, "proxy_url", lambda repo: "unused")
    monkeypatch.setattr(projects, "_exchange", lambda repo, url: None)
    assert store.sync("plant-pal")["slug"] == "plant-pal"


CHAT = {"id": "c1", "title": "Voice notes", "author": "bo", "last_activity": "2026-10-09T02:00:00+00:00",
        "turns": [{"role": "prompt", "text": "Build a recorder"}, {"role": "reply", "text": "Done: recorder.py"}]}


def test_a_summary_is_written_once_per_chat_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def fake_complete(prompt: str) -> dict[str, Any]:
        prompts.append(prompt)
        return {"result": "Bo built a recorder.", "total_cost_usd": 0.02}

    monkeypatch.setattr(summarize, "_complete", fake_complete)
    first = summarize.summarize(CHAT, tmp_path)
    again = summarize.summarize(CHAT, tmp_path)
    assert (first["summary"], first["cached"], again["cached"]) == ("Bo built a recorder.", False, True)
    assert len(prompts) == 1 and "TEAMMATE:\nBuild a recorder" in prompts[0]
    longer = {**CHAT, "turns": CHAT["turns"] + [{"role": "prompt", "text": "Now add playback"}]}
    assert summarize.summarize(longer, tmp_path)["cached"] is False
    assert len(prompts) == 2


def test_a_long_chat_keeps_its_start_and_its_latest_turns() -> None:
    turns = [{"role": "prompt", "text": "FIRST"}] + [{"role": "reply", "text": "x" * 1000}] * 100 + [{"role": "prompt", "text": "LAST"}]
    text = summarize.chat_as_text({**CHAT, "turns": turns})
    assert len(text) < summarize.MAX_CHAT_CHARS + 100
    assert "FIRST" in text and "LAST" in text and "middle of the chat left out" in text


def test_an_empty_chat_is_not_sent_to_claude(tmp_path: Path) -> None:
    with pytest.raises(GitHubError, match="no messages"):
        summarize.summarize({**CHAT, "turns": []}, tmp_path)
