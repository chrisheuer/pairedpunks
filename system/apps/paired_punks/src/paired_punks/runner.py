"""Private shared project spaces for hackathon partners: share chats, prompts, results and files, kept in sync through GitHub.

Services run from /home/user/workspace (the repo root). Conventions:

- Persistent state (anything written and read across runs -- cursors,
  caches, snapshots, user records): read and write it under ``DATA_DIR``
  (defined below), never a hardcoded ``data/.apps/paired-punks/`` at the
  call site. ``DATA_DIR`` defaults to ``data/.apps/paired-punks/`` but
  honors the ``PAIRED_PUNKS_DATA_DIR`` env var, so an editing agent can point a
  throwaway instance at a *copy* of the data instead of the live store
  (see the update-app skill). Do NOT use ``Path(__file__)``-based
  paths for state -- the bug to avoid is one process writing to
  ``/home/user/workspace/data/.apps/...`` while another reads from
  ``/home/user/workspace/system/apps/<pkg>/data/...``.
- Static assets shipped alongside this file (templates, default
  configs, bundled JSON): ``Path(__file__).parent / "assets/..."`` is
  fine and is the right pattern.
- Listen port: bind ``PORT`` (defined below), which defaults to this
  app's assigned port but honors the ``PAIRED_PUNKS_PORT`` env var, so
  an editing agent can boot a throwaway instance on a *spare* port
  alongside the live one (see the update-app skill). Never hardcode
  the port at the ``run_simple`` call.

This is a synchronous Flask app served by the threaded Werkzeug server.
The app owns its own browser origin (the forwarder routes
``http://paired-punks.<workspace-host>/`` straight to this port), so it serves
at ``/`` and root-absolute URLs, cookies, and service workers all work
unmodified -- nothing rewrites anything. Use ``flask_sock`` if you need
WebSockets.
"""

import io
import json
import os
import re
import tomllib
import urllib.request
import zipfile
from html import escape as html_escape
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, request, send_file
from werkzeug.serving import run_simple

from paired_punks import transcripts
from paired_punks.github import GitHubError
from paired_punks.projects import MAX_MEMBERS, Store
from paired_punks.projects import _kind as _file_kind

# Persistent state for this app lives under DATA_DIR. It defaults to
# ``data/.apps/paired-punks/`` but is overridable via the ``PAIRED_PUNKS_DATA_DIR`` env var
# so a throwaway instance can run against a *copy* of the data while editing --
# see the update-app skill. Always read/write state through DATA_DIR;
# never hardcode ``data/.apps/paired-punks/`` at a call site, or the override is
# bypassed. A writing call site should ``DATA_DIR.mkdir(parents=True,
# exist_ok=True)`` before writing.
DATA_DIR = Path(os.environ.get("PAIRED_PUNKS_DATA_DIR", "data/.apps/paired-punks"))

# Listen port. Defaults to this app's assigned port but is overridable via
# the ``PAIRED_PUNKS_PORT`` env var so an editing agent can boot a throwaway
# instance on a spare port next to the live one (see the update-app skill).
# Never hardcode the port at the ``run_simple`` call, or the override is bypassed.
PORT = int(os.environ.get("PAIRED_PUNKS_PORT", "8080"))

# The browser-side modules the workspace shell builds and every app serves from
# its own origin: the app contract (how a page talks to the shell framing it) and
# the element context menu (the right-click menu whose last rows hand the
# clicked element to a chat). A module import is a fetch without cookies, which
# the forwarder refuses across origins, so they are served here rather than from
# the shell. Relative to the repo root the service runs from, like DATA_DIR.
SHELL_STATIC_MODULES_DIR = Path("system/apps/system_interface/imbue/system_interface/static/_static")
SHELL_STATIC_MODULE_NAMES = ('app_contract.js', 'context_menu.js')

# The script every page serves (keep it on every page): it connects the page to
# the shell, reports where the page is on the handshake so the shell can reopen
# this app's window at the same place, and installs the element context menu.
# A page visited outside the shell runs it harmlessly: nothing arrives, and the
# menu's Explain and Modify rows grey out.
SHELL_PAGE_SCRIPT = """<script type="module">
  import { connectToShell } from "/_static/app_contract.js";
  import { installElementContextMenu } from "/_static/context_menu.js";
  let handshake = null;
  const connection = connectToShell({
    onHandshake: (received) => {
      handshake = received;
      connection.location(location.pathname + location.search, document.title);
    },
  });
  installElementContextMenu({ connection, handshake: () => handshake });
</script>"""

app = Flask("paired_punks", static_folder=None)

# Brand assets (fonts, logos) shipped alongside this file.
ASSETS_DIR = Path(__file__).parent / "static"


@app.route("/assets/<path:asset_path>")
def asset(asset_path: str) -> Response:
    target = (ASSETS_DIR / asset_path).resolve()
    if not target.is_file() or ASSETS_DIR.resolve() not in target.parents:
        abort(404)
    response = send_file(target)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/")
def index() -> Response:
    html = (Path(__file__).parent / "app.html").read_text()
    html = html.replace("__CHAT_LABEL__", _chat_app_label())
    return Response(html.replace("<!--SHELL_PAGE_SCRIPT-->", SHELL_PAGE_SCRIPT), mimetype="text/html")


ONBOARDING_PATH = Path(__file__).resolve().parents[2] / "ONBOARDING.md"


def _inline_markdown(text: str) -> str:
    text = html_escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)


@app.route("/guide")
def guide() -> Response:
    """The onboarding guide, rendered in the PairedPunks style."""
    parts, list_tag = [], ""
    for raw in ONBOARDING_PATH.read_text().splitlines():
        line = raw.strip()
        numbered = re.match(r"\d+\.\s+(.*)", line)
        bullet = re.match(r"-\s+(.*)", line)
        tag = "ol" if numbered else "ul" if bullet else ""
        if list_tag and tag != list_tag and not (raw.startswith("   ") and line):
            parts.append(f"</{list_tag}>")
            list_tag = ""
        if raw.startswith("   ") and line and list_tag:
            parts[-1] = parts[-1].replace("</li>", f"<br>{_inline_markdown(line)}</li>")
        elif tag:
            if not list_tag:
                parts.append(f"<{tag}>")
                list_tag = tag
            parts.append(f"<li>{_inline_markdown((numbered or bullet).group(1))}</li>")
        elif line.startswith("## "):
            parts.append(f"<h2>{_inline_markdown(line[3:])}</h2>")
        elif line.startswith("# "):
            parts.append(f"<h1>{_inline_markdown(line[2:])}</h1>")
        elif line:
            parts.append(f"<p>{_inline_markdown(line)}</p>")
    if list_tag:
        parts.append(f"</{list_tag}>")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Pair up with PairedPunks</title><style>
@font-face {{ font-family: "Fraunces"; font-weight: 900; font-style: italic; src: url(/assets/fonts/Fraunces-Italic900.ttf); }}
@font-face {{ font-family: "Big Shoulders Stencil Display"; font-weight: 800; src: url(/assets/fonts/BigShouldersStencil800.ttf); }}
@font-face {{ font-family: "DM Sans"; font-weight: 400; src: url(/assets/fonts/DMSans-400.ttf); }}
@font-face {{ font-family: "DM Sans"; font-weight: 700; src: url(/assets/fonts/DMSans-700.ttf); }}
@font-face {{ font-family: "JetBrains Mono"; font-weight: 500; src: url(/assets/fonts/JetBrainsMono-500.ttf); }}
body {{ margin: 0; background: #F3E9D6; color: #1A2744; font: 15px/1.6 "DM Sans", system-ui, sans-serif; }}
main {{ max-width: 680px; margin: 32px auto; background: #FBF6EC; border: 2px solid #1A2744; box-shadow: 8px 8px 0 #E2849F; padding: 28px 34px 30px; }}
h1 {{ font: italic 900 32px/1.1 "Fraunces", Georgia, serif; margin: 14px 0 10px; }}
h2 {{ font: 800 14px "Big Shoulders Stencil Display", sans-serif; letter-spacing: 3px; text-transform: uppercase; color: #1F5A43; margin: 26px 0 6px; border-top: 2px solid #1A2744; padding-top: 16px; }}
ol, ul {{ padding-left: 22px; margin: 6px 0; }} li {{ margin: 6px 0; }}
code {{ font: 500 13px "JetBrains Mono", monospace; background: #F3E9D6; padding: 1px 5px; }}
img {{ height: 64px; }} a {{ color: #1F5A43; font-weight: 700; }}
</style></head><body><main><img src="/assets/logos/pairedpunks-shield-mark.svg" alt="">{"".join(parts)}
<p><a href="/">Back to PairedPunks</a></p></main>{SHELL_PAGE_SCRIPT}</body></html>"""
    return Response(page, mimetype="text/html")


@app.route("/mock")
def mock() -> Response:
    html = (Path(__file__).parent / "mock.html").read_text()
    html = html.replace("__CHAT_LABEL__", _chat_app_label())
    return Response(html.replace("<!--SHELL_PAGE_SCRIPT-->", SHELL_PAGE_SCRIPT), mimetype="text/html")


def _chat_app_label() -> str:
    """The chat app's hostname label, so the page can embed the user's own chat."""
    entry = _chat_app_entry()
    return str(entry.get("label", "")) if entry else ""


def _chat_app_entry() -> dict[str, Any] | None:
    try:
        registry = tomllib.loads(Path("data/.state/apps.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    for entry in registry.get("apps", []):
        if entry.get("name") == "chat":
            return entry
    return None


# ---------------------------------------------------------------- API

STORE = Store(DATA_DIR)


@app.errorhandler(GitHubError)
def github_error(error: GitHubError) -> tuple[Response, int]:
    return jsonify({"error": str(error)}), 400


def _body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


@app.get("/api/me")
def api_me() -> Response:
    return jsonify(STORE.me())


@app.get("/api/projects")
def api_projects() -> Response:
    return jsonify({"projects": STORE.projects()})


@app.post("/api/projects")
def api_create_project() -> Response:
    body = _body()
    return jsonify(STORE.create(str(body.get("name", "")), str(body.get("event", "")).strip()))


@app.get("/api/invitations")
def api_invitations() -> Response:
    return jsonify({"invitations": STORE.invitations()})


@app.post("/api/projects/join")
def api_join() -> Response:
    body = _body()
    return jsonify(STORE.join(str(body["repo"]), body.get("invitation_id")))


@app.get("/api/projects/<slug>/state")
def api_state(slug: str) -> Response:
    return jsonify(STORE.state(slug))


@app.get("/api/projects/<slug>/members")
def api_members(slug: str) -> Response:
    return jsonify({"members": STORE.members(slug), "max": MAX_MEMBERS})


@app.post("/api/projects/<slug>/invite")
def api_invite(slug: str) -> Response:
    STORE.invite(slug, str(_body().get("login", "")))
    return jsonify({"members": STORE.members(slug), "max": MAX_MEMBERS})


@app.post("/api/projects/<slug>/remove")
def api_remove(slug: str) -> Response:
    body = _body()
    STORE.remove_member(slug, str(body.get("login", "")), body.get("invitation_id"))
    return jsonify({"members": STORE.members(slug), "max": MAX_MEMBERS})


@app.post("/api/projects/<slug>/sync")
def api_sync(slug: str) -> Response:
    return jsonify(STORE.sync(slug))


@app.get("/api/my-chats")
def api_my_chats() -> Response:
    return jsonify({"chats": transcripts.list_my_chats()})


@app.post("/api/projects/<slug>/share-chats")
def api_share_chats(slug: str) -> Response:
    for chat_id in _body().get("chat_ids", []):
        STORE.share_chat(slug, str(chat_id))
    return jsonify(STORE.sync(slug))


@app.post("/api/projects/<slug>/unshare-chat")
def api_unshare_chat(slug: str) -> Response:
    STORE.unshare_chat(slug, str(_body().get("chat_id", "")))
    return jsonify(STORE.sync(slug))


@app.post("/api/projects/<slug>/upload")
def api_upload(slug: str) -> Response:
    for uploaded in request.files.getlist("files"):
        STORE.add_file(slug, uploaded.filename or "file", uploaded.read())
    return jsonify(STORE.sync(slug))


@app.get("/api/workspace-files")
def api_workspace_files() -> Response:
    query = request.args.get("q", "").strip().lower()
    results = []
    for directory, subdirectories, filenames in os.walk("data"):
        subdirectories[:] = sorted(d for d in subdirectories if not d.startswith(".") and not (directory == "data" and d == "memories"))
        for name in [*subdirectories, *sorted(f for f in filenames if not f.startswith("."))]:
            path = Path(directory) / name
            if query and query not in str(path).lower():
                continue
            results.append({"path": str(path), "is_dir": path.is_dir()})
        if len(results) >= 60:
            break
    return jsonify({"files": results})


@app.post("/api/projects/<slug>/share-paths")
def api_share_paths(slug: str) -> Response:
    workspace = Path.cwd().resolve()
    for raw in _body().get("paths", []):
        source = (workspace / str(raw)).resolve()
        if workspace not in source.parents or not source.exists():
            raise GitHubError(f"{raw} is not in this workspace.")
        STORE.add_workspace_path(slug, source)
    return jsonify(STORE.sync(slug))


@app.get("/api/projects/<slug>/chat")
def api_chat(slug: str) -> Response:
    return jsonify(STORE.chat(slug, request.args.get("path", "")))


_TEXT_KINDS = {"code", "data", "file"}


@app.get("/api/projects/<slug>/file")
def api_file(slug: str) -> Response:
    target = STORE.safe_path(slug, request.args.get("path", ""))
    if not target.is_file():
        abort(404)
    kind = _file_kind(target.name)
    if kind in _TEXT_KINDS:
        # Shown as text, never rendered: a shared file could hold anything.
        response = send_file(target, mimetype="text/plain; charset=utf-8")
    else:
        response = send_file(target)
    response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/projects/<slug>/download")
def api_download(slug: str) -> Response:
    target = STORE.safe_path(slug, request.args.get("path", ""))
    if not target.is_file():
        abort(404)
    return send_file(target, as_attachment=True)


@app.get("/api/projects/<slug>/original")
def api_original(slug: str) -> Response:
    """A shared chat's original transcript, pretty-printed one event at a time."""
    target = STORE.safe_path(slug, request.args.get("path", ""))
    if not target.is_file():
        abort(404)
    blocks = []
    for line in target.read_text(errors="replace").splitlines():
        try:
            blocks.append(json.dumps(json.loads(line), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            blocks.append(line)
    body = "\n\n".join(html_escape(block) for block in blocks)
    page = ("<!doctype html><meta charset=utf-8><title>Original chat</title><style>body{margin:0;background:#FBF6EC;"
            "color:#1A2744;font:12px/1.5 ui-monospace,Menlo,monospace}pre{padding:16px 20px;white-space:pre-wrap}</style>"
            f"<pre>{body}</pre>")
    return Response(page, mimetype="text/html")


@app.get("/api/projects/<slug>/zip")
def api_zip(slug: str) -> Response:
    STORE.project(slug)
    repo = STORE.repo_dir(slug)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(repo.rglob("*")):
            # A link a partner committed would put the file it points at (on this machine) into the zip.
            if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(repo).parts:
                archive.write(path, Path(slug) / path.relative_to(repo))
    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=f"{slug}.zip")


@app.post("/api/my-chat/draft")
def api_my_chat_draft() -> Response:
    """Put text into the user's own chat's message box, unsent, and answer the chat page to show."""
    body = _body()
    entry = _chat_app_entry()
    if entry is None:
        raise GitHubError("Your chat isn't running right now.")
    intake = {"message": str(body.get("text", "")), "is_draft": True,
              "target": "new_chat" if body.get("new_chat") else "current_chat"}
    request_body = json.dumps(intake).encode()
    chat_request = urllib.request.Request(f"{entry['url'].rstrip('/')}/api/chats/intake", data=request_body,
                                          headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(chat_request, timeout=15) as response:
            answer = json.loads(response.read())
    except (OSError, ValueError) as error:
        raise GitHubError(f"Couldn't reach your chat: {error}") from error
    return jsonify({"path": answer.get("path", "/")})


@app.route("/_static/<basename>")
def shell_module(basename: str) -> Response:
    # The two shell-built modules and nothing else: a name that is not one of
    # them is a 404, so this route can never read outside that directory.
    if basename not in SHELL_STATIC_MODULE_NAMES:
        abort(404)
    module_path = SHELL_STATIC_MODULES_DIR / basename
    if not module_path.is_file():
        abort(404)
    # Flask resolves a relative path against the app's own directory, not the cwd.
    return send_file(module_path.absolute(), mimetype="text/javascript")


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
