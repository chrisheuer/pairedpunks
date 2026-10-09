"""GitHub access for PairedPunks, through the workspace's credential gateway.

No token ever enters this process: REST calls go through ``latchkey curl`` (the
gateway injects the user's GitHub credential), and git talks to GitHub through
the gateway's git proxy with the gateway's own auth headers.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

API = "https://api.github.com"


class GitHubError(Exception):
    """A GitHub call failed; the message is safe to show the user."""


def api(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 30) -> Any:
    """Call the GitHub REST API. Returns parsed JSON (or None for an empty reply)."""
    marker = "\n__PP_STATUS__:"
    command = ["latchkey", "curl", "-s", "-X", method, f"{API}{path}", "-H", "Accept: application/vnd.github+json",
               "-w", marker + "%{http_code}"]
    if body is not None:
        command += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitHubError(f"Could not reach GitHub: {error}") from error
    output, _, status_text = result.stdout.rpartition(marker)
    if not status_text.strip().isdigit():
        raise GitHubError("Could not reach GitHub. Check that your GitHub account is still connected.")
    status = int(status_text.strip())
    payload: Any = None
    if output.strip():
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = output
    if isinstance(payload, dict) and payload.get("latchkeyError"):
        raise GitHubError("GitHub access is not allowed yet. Approve GitHub access for this workspace and try again.")
    if status >= 400:
        message = payload.get("message") if isinstance(payload, dict) else str(payload)
        raise GitHubError(f"GitHub said: {message} ({status})")
    return payload


def proxy_url(repo_full_name: str) -> str:
    gateway = os.environ.get("LATCHKEY_GATEWAY", "")
    if not gateway:
        raise GitHubError("The GitHub gateway is not available to this app.")
    return f"{gateway}/gateway/https://github.com/{repo_full_name}.git"


def _gateway_git_options() -> list[str]:
    options = ["-c", f"http.extraHeader=X-Latchkey-Gateway-Password: {os.environ.get('LATCHKEY_GATEWAY_PASSWORD', '')}"]
    override = os.environ.get("LATCHKEY_GATEWAY_PERMISSIONS_OVERRIDE", "")
    if override:
        options += ["-c", f"http.extraHeader=X-Latchkey-Gateway-Permissions-Override: {override}"]
    return options


def git(repo_dir: Path | None, *args: str, remote: bool = False, timeout: float = 120, check: bool = True) -> str:
    """Run git. ``remote=True`` adds the gateway auth for commands that talk to GitHub."""
    command = ["git"] + (_gateway_git_options() if remote else []) + list(args)
    # Commits carry the repo's configured identity (the member's GitHub login), never one inherited from the caller.
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(command, cwd=repo_dir, capture_output=True, text=True, timeout=timeout, env=environment)
    except subprocess.TimeoutExpired as error:
        raise GitHubError("GitHub took too long to answer. Try again.") from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise GitHubError(detail[-1] if detail else f"git {args[0]} failed")
    return result.stdout
