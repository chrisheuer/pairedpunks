"""Catch-up summaries of a partner's shared chat, written by Claude.

Runs ``claude -p`` as a plain completion (no tools, from a throwaway folder so the
workspace's own agent instructions stay out of it), the keyless path from the
use-ai-integration skill. Summaries are kept per chat version, so asking again for
the same chat costs nothing until the partner adds to it.
"""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from paired_punks.github import GitHubError

SUMMARY_MODEL = "claude-haiku-4-5"
# Long chats keep their start and their latest turns; the middle is what gets cut.
MAX_CHAT_CHARS = 60_000
TIMEOUT_SECONDS = 180

SYSTEM = """You write catch-up notes for hackathon partners. A teammate worked with their AI assistant in the chat below.
Write a short catch-up for the reader, who did not see it:
- One sentence on what they were trying to do.
- Up to 5 bullets on what got decided, built or found, naming files and commands where they matter.
- One line starting "Open:" on what is unfinished or blocked, if anything.
Plain words, no preamble, no headings, under 150 words. Never repeat passwords, keys or tokens even if the chat shows them."""


def chat_version(chat: dict[str, Any]) -> str:
    turns = chat.get("turns") or []
    return hashlib.sha256(json.dumps([chat.get("last_activity"), len(turns)]).encode()).hexdigest()[:16]


def chat_as_text(chat: dict[str, Any]) -> str:
    lines = [f"Chat title: {chat.get('title', '')}", f"Teammate: {chat.get('author', '')}", ""]
    for turn in chat.get("turns") or []:
        label = "TEAMMATE" if turn.get("role") == "prompt" else "AI"
        lines.append(f"{label}:\n{turn.get('text', '')}\n")
    text = "\n".join(lines)
    if len(text) > MAX_CHAT_CHARS:
        head = MAX_CHAT_CHARS // 4
        text = text[:head] + "\n\n[... middle of the chat left out ...]\n\n" + text[-(MAX_CHAT_CHARS - head):]
    return text


def summarize(chat: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    """The catch-up for this version of the chat: from the cache, or freshly written."""
    version = chat_version(chat)
    key = hashlib.sha256(f"{chat.get('author')}/{chat.get('id')}".encode()).hexdigest()[:24]
    cache_file = cache_dir / f"{key}.json"
    cached = _read(cache_file)
    if cached and cached.get("version") == version:
        return {**cached, "cached": True}
    if not chat.get("turns"):
        raise GitHubError("This chat has no messages to summarize yet.")
    result = _complete(chat_as_text(chat))
    record = {"version": version, "summary": result.get("result", "").strip(), "model": SUMMARY_MODEL,
              "cost_usd": result.get("total_cost_usd"), "chat_id": chat.get("id"), "author": chat.get("author")}
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp = cache_file.with_suffix(".tmp")
    temp.write_text(json.dumps({**record, "raw": result}, indent=2))
    temp.replace(cache_file)
    return {**record, "cached": False}


def _complete(prompt: str) -> dict[str, Any]:
    command = ["claude", "-p", prompt, "--output-format", "json", "--model", SUMMARY_MODEL,
               "--system-prompt", SYSTEM, "--tools", "", "--no-session-persistence"]
    environment = dict(os.environ)
    environment.pop("MAIN_CLAUDE_SESSION_ID", None)
    if not environment.get("CLAUDE_CONFIG_DIR"):
        account = _default_account_dir()
        if account:
            environment["CLAUDE_CONFIG_DIR"] = account
    with tempfile.TemporaryDirectory(prefix="pp_summary_") as folder:
        try:
            finished = subprocess.run(command, capture_output=True, text=True, env=environment, cwd=folder,
                                      timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubError(f"Couldn't write the catch-up: {error}") from error
    try:
        data = json.loads(finished.stdout)
    except json.JSONDecodeError:
        data = None
    if finished.returncode != 0 or not isinstance(data, dict) or data.get("is_error") or not data.get("result"):
        detail = (data or {}).get("result") if isinstance(data, dict) else (finished.stderr or "").strip()[:200]
        raise GitHubError(f"Couldn't write the catch-up. {detail or 'Claude did not answer.'}")
    return data


def _default_account_dir() -> str:
    """The workspace's most recently used Claude account (a service has no account of its own)."""
    root = Path.home() / ".minds" / "accounts"
    index = _read(root / "index.json") or {}
    rows = [a for a in index.get("accounts", []) if isinstance(a, dict) and a.get("lane") == "anthropic"]
    if not rows:
        return ""
    chosen = next((a for a in rows if a.get("id") == index.get("mru")), rows[0])
    path = root / str(chosen.get("id", ""))
    return str(path) if path.is_dir() else ""


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
