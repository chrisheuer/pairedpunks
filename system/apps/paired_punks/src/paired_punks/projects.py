"""PairedPunks projects: one private GitHub repository per project, cloned locally.

Repository layout (the shared format every member's app reads and writes):

    README.md                               what this project is and how it is laid out
    .pairedpunks/project.json               name, event, who started it
    chats/<login>/<chat-id>.json            a shared chat as prompt/reply turns
    chats/<login>/<chat-id>.events.jsonl    the chat's original transcript, unprocessed
    artifacts/<login>/<chat-id>/<path>      files that chat's agent wrote
    files/<login>/<name>                    files a member shared or uploaded
    anything else                           work the team checked in directly (code, images, media)

Each member writes only under their own login, so syncs rarely conflict.
"""

import json
import random
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paired_punks import transcripts
from paired_punks.github import GitHubError, api, git, proxy_url

MAX_MEMBERS = 10
REPO_PREFIX = "pp-"
FORMAT_VERSION = 1
FILE_MAX_BYTES = 25 * 1024 * 1024
# When a partner syncs at the same moment, their push lands between our fetch and our push. We then wait for
# the shared copy to stop moving and go again, up to this many rounds.
SYNC_ROUNDS = 6
PARTNER_POLL_SECONDS = 0.75
PARTNER_SETTLE_POLLS = 8

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock(slug: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(slug, threading.Lock())


class Store:
    """Local state: the project list, which chats I share, and the clones."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.repos_dir = data_dir / "repos"
        self._me: dict[str, Any] | None = None

    # ----- local records -----
    def _read(self, name: str, default: Any) -> Any:
        try:
            return json.loads((self.data_dir / name).read_text())
        except (OSError, json.JSONDecodeError):
            return default

    def _write(self, name: str, value: Any) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.data_dir / f".{name}.tmp"
        temp.write_text(json.dumps(value, indent=2))
        temp.replace(self.data_dir / name)

    def projects(self) -> list[dict[str, Any]]:
        return self._read("projects.json", [])

    def project(self, slug: str) -> dict[str, Any]:
        for project in self.projects():
            if project["slug"] == slug:
                return project
        raise GitHubError("That project is not on this workspace.")

    def _save_project(self, record: dict[str, Any]) -> None:
        projects = [p for p in self.projects() if p["slug"] != record["slug"]]
        projects.append(record)
        self._write("projects.json", projects)

    def shared_chats(self, slug: str) -> list[str]:
        return self._read("shared_chats.json", {}).get(slug, [])

    def _set_shared_chats(self, slug: str, chat_ids: list[str]) -> None:
        shared = self._read("shared_chats.json", {})
        shared[slug] = chat_ids
        self._write("shared_chats.json", shared)

    def repo_dir(self, slug: str) -> Path:
        return self.repos_dir / slug

    # ----- identity -----
    def me(self) -> dict[str, Any]:
        if self._me is None:
            user = api("GET", "/user")
            self._me = {"login": user["login"], "id": user["id"], "name": user.get("name") or user["login"],
                        "avatar_url": user.get("avatar_url")}
        return self._me

    def _configure_identity(self, repo: Path) -> None:
        me = self.me()
        git(repo, "config", "user.name", me["login"])
        git(repo, "config", "user.email", f"{me['id']}+{me['login']}@users.noreply.github.com")

    # ----- create / join -----
    def create(self, name: str, event: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise GitHubError("Give the project a name.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-").removeprefix(REPO_PREFIX)[:60] or "project"
        repo_name = REPO_PREFIX + slug
        me = self.me()
        created = api("POST", "/user/repos", {
            "name": repo_name, "private": True, "auto_init": False, "has_issues": False, "has_wiki": False,
            "description": f"PairedPunks project: {name}" + (f" ({event})" if event else ""),
        })
        full_name = created["full_name"]
        repo = self.repo_dir(slug)
        if repo.exists():
            shutil.rmtree(repo)
        repo.mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        self._configure_identity(repo)
        (repo / ".pairedpunks").mkdir()
        (repo / ".pairedpunks" / "project.json").write_text(json.dumps({
            "name": name, "event": event, "created_by": me["login"], "created_at": _now(), "format": FORMAT_VERSION,
        }, indent=2) + "\n")
        (repo / "README.md").write_text(_readme(name, event))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"Start PairedPunks project: {name}")
        git(repo, "push", "-q", proxy_url(full_name), "main", remote=True)
        record = {"slug": slug, "repo": full_name, "name": name, "event": event, "last_sync": _now()}
        self._save_project(record)
        return record

    def invitations(self) -> list[dict[str, Any]]:
        """Pending invitations to PairedPunks projects, plus projects I can open but have not cloned here."""
        local = {p["repo"] for p in self.projects()}
        pending = [{"invitation_id": inv["id"], "repo": inv["repository"]["full_name"],
                    "from": (inv.get("inviter") or {}).get("login", ""), "pending": True}
                   for inv in api("GET", "/user/repository_invitations") or []
                   if inv["repository"]["name"].startswith(REPO_PREFIX)]
        available = [{"repo": r["full_name"], "from": r["owner"]["login"], "pending": False}
                     for r in api("GET", "/user/repos?affiliation=owner,collaborator&per_page=100&sort=updated") or []
                     if r["name"].startswith(REPO_PREFIX) and r["full_name"] not in local]
        return pending + available

    def join(self, repo_full_name: str, invitation_id: int | None) -> dict[str, Any]:
        if invitation_id:
            api("PATCH", f"/user/repository_invitations/{int(invitation_id)}")
        slug = re.sub(r"[^a-z0-9]+", "-", repo_full_name.split("/")[-1].removeprefix(REPO_PREFIX).lower()).strip("-")
        if any(p["slug"] == slug for p in self.projects()):
            slug = f"{slug}-{repo_full_name.split('/')[0].lower()}"
        repo = self.repo_dir(slug)
        if repo.exists():
            shutil.rmtree(repo)
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        git(None, "clone", "-q", proxy_url(repo_full_name), str(repo), remote=True)
        self._configure_identity(repo)
        git(repo, "remote", "remove", "origin")
        meta = _read_json(repo / ".pairedpunks" / "project.json") or {}
        record = {"slug": slug, "repo": repo_full_name, "name": meta.get("name") or slug, "event": meta.get("event", ""),
                  "last_sync": _now()}
        self._save_project(record)
        return record

    # ----- members -----
    def members(self, slug: str) -> list[dict[str, Any]]:
        project = self.project(slug)
        repo = project["repo"]
        owner = repo.split("/")[0]
        members = [{"login": c["login"], "avatar_url": c.get("avatar_url"), "pending": False,
                    "owner": c["login"].lower() == owner.lower()}
                   for c in api("GET", f"/repos/{repo}/collaborators?per_page=100") or []]
        try:
            for invitation in api("GET", f"/repos/{repo}/invitations") or []:
                invitee = invitation.get("invitee") or {}
                members.append({"login": invitee.get("login", "?"), "avatar_url": invitee.get("avatar_url"),
                                "pending": True, "owner": False, "invitation_id": invitation["id"]})
        except GitHubError:
            pass  # only the owner can see pending invitations
        return members

    def invite(self, slug: str, login: str) -> None:
        login = login.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
            raise GitHubError("That doesn't look like a GitHub username.")
        if len(self.members(slug)) >= MAX_MEMBERS:
            raise GitHubError(f"A project holds up to {MAX_MEMBERS} people. Remove someone first.")
        try:
            api("PUT", f"/repos/{self.project(slug)['repo']}/collaborators/{login}", {"permission": "push"})
        except GitHubError as error:
            if "(404)" in str(error):
                raise GitHubError(f"There's no GitHub user called {login}. Check the spelling.") from error
            raise

    def remove_member(self, slug: str, login: str, invitation_id: int | None) -> None:
        repo = self.project(slug)["repo"]
        if invitation_id:
            api("DELETE", f"/repos/{repo}/invitations/{int(invitation_id)}")
        else:
            api("DELETE", f"/repos/{repo}/collaborators/{login}")

    # ----- sharing -----
    def share_chat(self, slug: str, chat_id: str) -> None:
        shared = self.shared_chats(slug)
        if chat_id not in shared:
            self._set_shared_chats(slug, shared + [chat_id])
        with _lock(slug):
            self._export_chat(slug, chat_id)

    def unshare_chat(self, slug: str, chat_id: str) -> None:
        self._set_shared_chats(slug, [c for c in self.shared_chats(slug) if c != chat_id])
        login = self.me()["login"]
        repo = self.repo_dir(slug)
        chat_dir = _own_dir(repo, "chats", login)
        for path in [chat_dir / f"{chat_id}.json", chat_dir / f"{chat_id}.events.jsonl"]:
            path.unlink(missing_ok=True)
        artifacts = _own_dir(repo, "artifacts", login) / chat_id
        if artifacts.is_symlink():
            artifacts.unlink()
        else:
            shutil.rmtree(artifacts, ignore_errors=True)

    def _export_chat(self, slug: str, chat_id: str) -> None:
        exported = transcripts.export_chat(chat_id)
        if exported is None:
            return
        login = self.me()["login"]
        repo = self.repo_dir(slug)
        chat_dir = _own_dir(repo, "chats", login)
        artifact_records = []
        for source in exported["artifact_paths"]:
            if not _shareable(source):
                continue
            relative = _relative_to_workspace(source)
            target = _own_dir(repo, "artifacts", login, chat_id, *relative.parent.parts) / relative.name
            if target.is_symlink():
                target.unlink()
            shutil.copy2(source, target)
            artifact_records.append({"path": str(target.relative_to(repo)), "source_path": str(source)})
        for name in (f"{chat_id}.json", f"{chat_id}.events.jsonl"):
            if (chat_dir / name).is_symlink():
                (chat_dir / name).unlink()
        (chat_dir / f"{chat_id}.json").write_text(json.dumps({
            "id": chat_id, "title": exported["title"], "author": login, "shared_at": _now(),
            "last_activity": exported["last_activity"], "turns": exported["turns"], "artifacts": artifact_records,
            "raw": f"chats/{login}/{chat_id}.events.jsonl",
        }, indent=2) + "\n")
        (chat_dir / f"{chat_id}.events.jsonl").write_text("\n".join(exported["raw_lines"]) + "\n")

    def add_file(self, slug: str, name: str, data: bytes) -> str:
        if len(data) > FILE_MAX_BYTES:
            raise GitHubError("That file is over 25 MB. Share something smaller.")
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip() or "file"
        target = _own_dir(self.repo_dir(slug), "files", self.me()["login"]) / safe
        if target.is_symlink():
            target.unlink()
        target.write_bytes(data)
        return str(target.relative_to(self.repo_dir(slug)))

    def add_workspace_path(self, slug: str, source: Path) -> list[str]:
        """Copy a workspace file or folder into files/<me>/."""
        repo = self.repo_dir(slug)
        login = self.me()["login"]
        added = []
        if not _shareable(source):
            raise GitHubError(f"{_relative_to_workspace(source)} is private to this workspace and can't be shared.")
        sources = [source] if source.is_file() else [p for p in source.rglob("*")
                                                     if p.is_file() and ".git" not in p.parts and _shareable(p)]
        total = sum(p.stat().st_size for p in sources)
        if total > 4 * FILE_MAX_BYTES:
            raise GitHubError("That folder is over 100 MB. Share something smaller.")
        for path in sources:
            relative = Path(source.name) / path.relative_to(source) if source.is_dir() else Path(source.name)
            target = _own_dir(repo, "files", login, *relative.parent.parts) / relative.name
            if target.is_symlink():
                target.unlink()
            shutil.copy2(path, target)
            added.append(str(target.relative_to(repo)))
        return added

    # ----- sync -----
    def sync(self, slug: str) -> dict[str, Any]:
        project = self.project(slug)
        repo = self.repo_dir(slug)
        url = proxy_url(project["repo"])
        # The per-project lock is the queue for this workspace: a second sync (the timer, another window)
        # waits here for the one in progress, then runs with whatever changed meanwhile.
        with _lock(slug):
            for chat_id in self.shared_chats(slug):
                self._export_chat(slug, chat_id)
            git(repo, "add", "-A")
            if git(repo, "status", "--porcelain").strip():
                git(repo, "commit", "-q", "-m", f"Sync from {self.me()['login']}")
            _exchange(repo, url)
        project["last_sync"] = _now()
        self._save_project(project)
        return project

    # ----- reading -----
    def state(self, slug: str) -> dict[str, Any]:
        project = self.project(slug)
        repo = self.repo_dir(slug)
        last_change = _last_changes(repo)
        login = self.me()["login"]
        chats, artifacts, files, tree = [], [], [], []
        for chat_file in sorted((repo / "chats").glob("*/*.json")):
            chat = _read_json(chat_file)
            if not chat:
                continue
            last = _parse_time(chat.get("last_activity"))
            chats.append({"id": chat["id"], "title": chat.get("title") or chat["id"], "author": chat.get("author"),
                          "path": str(chat_file.relative_to(repo)), "last_activity": chat.get("last_activity"),
                          "live": last is not None and transcripts.is_live(last), "turns": len(chat.get("turns", []))})
        for path in sorted(p for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts):
            relative = str(path.relative_to(repo))
            parts = Path(relative).parts
            author, when = last_change.get(relative, (login, None))
            entry = {"path": relative, "name": path.name, "author": author, "updated": when,
                     "kind": _kind(path.name), "size": path.stat().st_size}
            if parts[0] == "artifacts" and len(parts) >= 4:
                entry.update(author=parts[1], chat_id=parts[2])
                artifacts.append(entry)
            elif parts[0] == "files" and len(parts) >= 3:
                entry["author"] = parts[1]
                files.append(entry)
            if parts[0] not in ("chats", ".pairedpunks"):
                tree.append(entry)
        return {"project": project, "me": self.me(), "chats": chats, "artifacts": artifacts, "files": files,
                "tree": tree, "shared_chats": self.shared_chats(slug), "local_path": str(repo.resolve())}

    def chat(self, slug: str, path: str) -> dict[str, Any]:
        return _read_json(self.safe_path(slug, path)) or {}

    def safe_path(self, slug: str, path: str) -> Path:
        self.project(slug)  # only a project on this workspace, never a folder named by the URL
        repo = self.repo_dir(slug).resolve()
        target = (repo / path).resolve()
        if repo not in target.parents or ".git" in target.relative_to(repo).parts:
            raise GitHubError("That file is not part of this project.")
        return target


def _exchange(repo: Path, url: str) -> None:
    """Pull partners' work in and push ours out, waiting our turn while a partner is mid-sync."""
    for round_number in range(SYNC_ROUNDS):
        git(repo, "fetch", "-q", url, "main", remote=True)
        fetched = git(repo, "rev-parse", "FETCH_HEAD").strip()
        if not subprocess_merge(repo):
            raise GitHubError("Your changes and a partner's changes touch the same file. "
                              "Open the project on GitHub to sort it out, then sync again.")
        try:
            git(repo, "push", "-q", url, "HEAD:main", remote=True)
            return
        except GitHubError:
            if _remote_head(url) == fetched:
                raise  # nobody else pushed: a real failure, not a partner syncing
        # A partner pushed between our fetch and our push, so they are syncing right now.
        _wait_for_partner(url, round_number)
    raise GitHubError("Your partners are syncing a lot right now. Give it a minute and sync again.")


def _remote_head(url: str) -> str:
    line = git(None, "ls-remote", url, "refs/heads/main", remote=True).strip()
    return line.split()[0] if line else ""


def _wait_for_partner(url: str, round_number: int) -> None:
    """Wait until the shared copy has stopped changing (the partner's sync finished), then a random beat more,
    so two partners who collided don't collide again."""
    pause = threading.Event()
    previous = _remote_head(url)
    for _ in range(PARTNER_SETTLE_POLLS):
        pause.wait(PARTNER_POLL_SECONDS)
        current = _remote_head(url)
        if current == previous:
            break
        previous = current
    pause.wait(random.uniform(0, 0.5 * (round_number + 1)))


def _own_dir(repo: Path, *parts: str) -> Path:
    """repo/<parts...>, created as real folders. Refuses a path a partner turned into a link: following it
    would write outside the project (git won't follow one, but plain file writes would)."""
    current = repo
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise GitHubError(f"{current.relative_to(repo)} in this project is a link, not a folder, so PairedPunks "
                              "won't write there. Ask your partners to remove it.")
        current.mkdir(exist_ok=True)
    return current


def _shareable(path: Path) -> bool:
    """A workspace file it's safe to copy into a project: not a link, and not private workspace state."""
    return not path.is_symlink() and transcripts._workspace_path(str(path)) is not None


def subprocess_merge(repo: Path) -> bool:
    """Merge the fetched main into ours. Returns False (and leaves the tree clean) on a conflict."""
    has_head = git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    if not has_head:
        git(repo, "reset", "-q", "--hard", "FETCH_HEAD")
        return True
    try:
        git(repo, "merge", "-q", "--no-edit", "FETCH_HEAD")
        return True
    except GitHubError:
        git(repo, "merge", "--abort", check=False)
        return False


def _last_changes(repo: Path) -> dict[str, tuple[str, str]]:
    """Path -> (author login, ISO time) of the last commit that touched it."""
    changes: dict[str, tuple[str, str]] = {}
    output = git(repo, "-c", "core.quotepath=off", "log", "--format=@@%an|%aI", "--name-only", check=False)
    author, when = "", ""
    for line in output.splitlines():
        if line.startswith("@@"):
            author, when = line[2:].split("|", 1)
        elif line.strip() and line not in changes:
            changes[line] = (author, when)
    return changes


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_KINDS = {
    "code": {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sh", ".go", ".rs", ".java", ".rb", ".c", ".cpp",
             ".h", ".swift", ".kt", ".sql", ".toml", ".yaml", ".yml"},
    "data": {".json", ".jsonl", ".csv", ".tsv", ".parquet", ".xml"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"},
    "media": {".mp3", ".m4a", ".wav", ".ogg", ".mp4", ".mov", ".webm"},
}


def _kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    for kind, suffixes in _KINDS.items():
        if suffix in suffixes:
            return kind
    return "file"


def _relative_to_workspace(path: Path) -> Path:
    for root in transcripts.WORKSPACE_ROOTS:
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    return Path(path.name)


def _readme(name: str, event: str) -> str:
    return f"""# {name}

A PairedPunks project{f" from {event}" if event else ""}: a private space where hackathon partners share
their AI chats, prompts, replies and files next to the code.

- `chats/<member>/` shared chats, as prompt and reply turns (`.json`) plus the original transcript (`.events.jsonl`)
- `artifacts/<member>/<chat>/` files an agent wrote during a shared chat
- `files/<member>/` files a member shared
- everything else is work the team checked in directly

Clone it with `git clone https://github.com/<owner>/<repo>.git`, or open it in PairedPunks.
"""
