---
title: "PairedPunks"
description: "Private shared project spaces for hackathon partners: share AI chats, prompts, replies and files with up to 10 people, kept in sync through a private GitHub project."
thumbnail: "template.svg"
version: v2
format: v2
---

# PairedPunks

This file is the manifest for the **PairedPunks** template (slug:
`pairedpunks`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Private shared project spaces for hackathon partners: share AI chats, prompts, replies and files with up to 10 people, kept in sync through a private GitHub project.

PairedPunks gives a hackathon team of 2 to 10 people one private shared space for the AI work they do separately. Each member runs the PairedPunks window in their own Imbue Studio workspace; from it they share their Studio chats (every prompt and reply, the files the agent wrote, and the raw transcript), workspace files and uploads into a project that everyone on the team sees. The window shows the member's own embedded chat as the first tab and one tab per partner with that partner's shared chats, a sidebar that groups chats, artifacts and files by type, by person, or as the project's folder tree, and a side-by-side view. Any prompt or reply can be copied, or added as a draft to the member's own chat to build on. Behind the scenes each project is a private GitHub repository (named `pp-<project-slug>`) owned by the member who started it, with the partners invited as collaborators; the app commits and syncs it for them, so nobody has to touch git.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/paired_punks`
- `system/supervisord.conf.d/paired-punks.conf`

- `system/apps/paired_punks` -- the PairedPunks app: a synchronous Flask package (`paired_punks`) with its `app.toml` manifest, `icon.svg`, the single-page UI (`app.html`), brand fonts and logos under `static/`, the pair-up guide `ONBOARDING.md` (also served as an in-app page), and a ratchet test. Its modules are:
  - `runner.py` -- the HTTP server and JSON API the page calls (projects, members, sharing, sync, clone-to-my-computer zip, "Add to my chat").
  - `projects.py` -- the project store: one local clone per project under `data/.apps/paired-punks/`, plus create / invite / join / share / sync, including merging partners' pushes.
  - `github.py` -- every GitHub call: REST through `latchkey curl`, and git clone/fetch/push through the latchkey gateway's git proxy (gateway auth is passed as extra HTTP headers; no GitHub token ever enters the process).
  - `transcripts.py` -- reads this workspace's own Studio chats from the local mngr transcripts (`~/.mngr/agents/<agent-id>/events/claude/common_transcript/events.jsonl`) and exports one as prompt/reply turns plus the files it wrote.
- `system/supervisord.conf.d/paired-punks.conf` -- the supervisord program `paired-punks`. It registers the app with `system/scripts/forward_port.py --manifest system/apps/paired_punks/app.toml --url http://localhost:8080` (so the window is served at its own `paired-punks.<workspace-host>` origin) and then runs the `paired-punks` console script, which listens on 127.0.0.1:8080 (`PAIRED_PUNKS_PORT` overrides it).

At runtime the shell starts the program when a PairedPunks window opens and stops it a minute after the last one closes (`stop_when_no_windows = true`). While the window is open the page asks the server to sync every 2 minutes (and on the Sync button): it commits the member's shared items into their folder of the project clone, fetches and merges partners' pushes, and pushes back. "Add to my chat" posts the text as a draft to the chat app's `/api/chats/intake` endpoint, found through the workspace's app registry. All state lives under `data/.apps/paired-punks/` (`PAIRED_PUNKS_DATA_DIR` overrides it); nothing else in the workspace is modified.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting agent must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.


Activation -- the app reaches GitHub only through latchkey, so the adopter must approve these before it can do anything:

- requires_permission: github-rest-api / github-read-user (user-approved; adopting agent initiates during setup) -- `GET /user`, to learn the member's GitHub login, which is their PairedPunks name.
- requires_permission: github-rest-api / github-read-repos (user-approved; adopting agent initiates during setup) -- `GET /user/repos`, `GET /user/repository_invitations`, and reading a project's collaborators and pending invitations.
- requires_permission: github-rest-api / github-write-repos (user-approved; adopting agent initiates during setup) -- `PUT`/`DELETE /repos/{owner}/{repo}/collaborators/{user}` and `DELETE /repos/{owner}/{repo}/invitations/{id}`, to invite and remove partners.
- requires_permission: github-rest-api / github-write-all (user-approved; adopting agent initiates during setup) -- `POST /user/repos` (start a project) and `PATCH /user/repository_invitations/{id}` (join one) fall outside `github-write-repos`, whose schema only matches `/repos/...` paths.
- requires_permission: github-git / github-git-read (user-approved; adopting agent initiates during setup) -- git clone and fetch of the project repositories through the gateway's git proxy.
- requires_permission: github-git / github-git-write (user-approved; adopting agent initiates during setup) -- git push of the member's shared items.

There are no secrets to set, and the app does not call an LLM (no model access is needed).

Adaptation -- things that do not work the way an adopter might expect and need a decision:

- No automatic chat monitoring: a chat reaches partners only after the member shares it with the upload button, and later turns only travel on a sync (each sync re-exports the member's shared chats). Nothing watches chats for updates or tells partners something changed. A background watcher that publishes chat updates is planned for v2; until then, decide whether sync-while-open is enough.
- Sync conflicts are not resolved: if a member's changes and a partner's pushed changes touch the same file, the merge is aborted and the sync stops with a message telling the member to sort it out on GitHub and sync again. Each member's chats and files go into their own folder of the project, so this only happens when two people edit the same file; agree on who owns which files, or add a resolution step.
- "Live" status is approximate: a partner's chat shows as live when its transcript changed in the last 15 minutes, not from a real presence signal. Change `LIVE_WINDOW_SECONDS` in `transcripts.py` if a different threshold suits the team.
- Sync runs only while a PairedPunks window is open (every 2 minutes) or when the member clicks Sync; with the window closed, nothing syncs. To sync in the background, set `stop_when_no_windows = false` in `app.toml` and add a server-side timer.
- The pair-up guide (`ONBOARDING.md`, also the in-app guide page) tells partners to add the template from the original publisher's GitHub URL; point it at the adopter's own copy if they publish or fork it.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment. The app shells out only to `git` and `latchkey`, both part of the stock workspace image, and its Python dependencies (Flask, Werkzeug) are declared in its own `pyproject.toml`; it is a uv workspace member (`system/apps/*`), so the workspace's normal `uv sync --all-packages` installs them.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new agent. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the Imbue Studio app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-10-09) -- first release: the PairedPunks app (private shared projects for 2-10 hackathon partners, synced through private GitHub repositories) and its supervisord program.

### v2 (2026-10-09) -- Safety fixes for partner-shared content (crafted file names, links committed by a partner, sandboxed file views), retry when a partner is syncing at the same moment, and a fix for the project menu freezing.

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
