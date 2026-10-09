"""Read this workspace's Studio chats so they can be shared into a project.

Each chat agent keeps its conversation at
``<mngr host dir>/agents/<agent-id>/events/claude/common_transcript/events.jsonl``
(one JSON "step" per line: the user's messages, the agent's replies, and the
tool calls it made). A chat the chat app moved between agents has several
agents sharing one ``chat_id`` label; they are read in ``chat_seq`` order.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MNGR_HOST_DIR = Path(os.environ.get("MNGR_HOST_DIR", "/home/user/.mngr"))
WORKSPACE_ROOTS = (Path("/home/user/workspace"), Path("/mngr-vol/home/workspace"))
ARTIFACT_MAX_BYTES = 5 * 1024 * 1024
LIVE_WINDOW_SECONDS = 15 * 60

_SEED = re.compile(r"<chat-seed-context>.*?</chat-seed-context>\s*", re.S)
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.S)


def _transcript_path(agent_dir: Path) -> Path | None:
    for candidate in sorted(agent_dir.glob("events/*/common_transcript/events.jsonl")):
        if "/logs/" not in str(candidate):
            return candidate
    return None


def _agent_records() -> list[dict[str, Any]]:
    records = []
    for agent_dir in sorted((MNGR_HOST_DIR / "agents").glob("agent-*")):
        try:
            data = json.loads((agent_dir / "data.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        labels = data.get("labels") or {}
        transcript = _transcript_path(agent_dir)
        if transcript is None or not labels.get("display_name") or labels.get("user_created") != "true":
            continue
        records.append({
            "agent_id": agent_dir.name,
            "chat_id": labels.get("chat_id") or agent_dir.name,
            "chat_seq": int(labels.get("chat_seq") or 1),
            "title": labels.get("display_name"),
            "archived": data.get("name", "").startswith("archived-"),
            "transcript": transcript,
        })
    return records


def _chats_by_id() -> dict[str, dict[str, Any]]:
    chats: dict[str, dict[str, Any]] = {}
    for record in _agent_records():
        chat = chats.setdefault(record["chat_id"], {"id": record["chat_id"], "agents": [], "title": record["title"]})
        chat["agents"].append(record)
        if not record["archived"]:
            chat["title"] = record["title"]
    for chat in chats.values():
        chat["agents"].sort(key=lambda r: r["chat_seq"])
        chat["last_activity"] = max(r["transcript"].stat().st_mtime for r in chat["agents"])
    return chats


def list_my_chats() -> list[dict[str, Any]]:
    """This workspace's chats, newest first: id, title, last activity."""
    chats = sorted(_chats_by_id().values(), key=lambda c: c["last_activity"], reverse=True)
    return [{"id": c["id"], "title": c["title"], "last_activity": _iso(c["last_activity"]),
             "live": is_live(c["last_activity"])} for c in chats]


def is_live(timestamp: float) -> bool:
    return datetime.now(timezone.utc).timestamp() - timestamp < LIVE_WINDOW_SECONDS


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _clean_prompt(text: str) -> str:
    return _REMINDER.sub("", _SEED.sub("", text)).strip()


def _workspace_path(raw: str) -> Path | None:
    path = Path(raw)
    if not path.is_absolute():
        return None
    for root in WORKSPACE_ROOTS:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if relative.parts[:2] in (("data", ".secrets"), ("data", ".state"), ("data", "memories")) or ".git" in relative.parts:
            return None
        return path
    return None


def export_chat(chat_id: str) -> dict[str, Any] | None:
    """One chat as prompt/reply turns, plus the files its agent wrote and the raw transcript lines."""
    chat = _chats_by_id().get(chat_id)
    if chat is None:
        return None
    turns: list[dict[str, Any]] = []
    artifacts: list[str] = []
    raw_lines: list[str] = []
    for record in chat["agents"]:
        for line in record["transcript"].read_text(errors="replace").splitlines():
            raw_lines.append(line)
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue
            if step.get("type") != "step":
                continue
            source = step.get("source")
            message = step.get("message") if isinstance(step.get("message"), str) else ""
            if source == "user":
                text = _clean_prompt(message)
                if text:
                    turns.append({"role": "prompt", "text": text, "at": step.get("timestamp")})
            elif source == "agent":
                for call in step.get("tool_calls") or []:
                    if call.get("function_name") in ("Write", "Edit", "NotebookEdit"):
                        target = (call.get("arguments") or {}).get("file_path", "")
                        if target and target not in artifacts:
                            artifacts.append(target)
                if message.strip():
                    if turns and turns[-1]["role"] == "reply":
                        turns[-1]["text"] += "\n\n" + message.strip()
                        turns[-1]["at"] = step.get("timestamp")
                    else:
                        turns.append({"role": "reply", "text": message.strip(), "at": step.get("timestamp")})
    files = []
    for raw in artifacts:
        path = _workspace_path(raw)
        if path is not None and path.is_file() and path.stat().st_size <= ARTIFACT_MAX_BYTES:
            files.append(path)
    return {
        "id": chat_id,
        "title": chat["title"],
        "last_activity": _iso(chat["last_activity"]),
        "turns": turns,
        "artifact_paths": files,
        "raw_lines": raw_lines,
    }
