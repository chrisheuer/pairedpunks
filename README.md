<p align="center">
  <img alt="PairedPunks" src="template.svg" width="480">
</p>

# PairedPunks

> **Requires Imbue Studio (beta).** PairedPunks only runs inside an Imbue Studio workspace, and Studio is still in beta. It is not a standalone app and won't run on its own from this code. You and every partner need your own Studio workspace and a GitHub account.

<p align="center">
  <a href="https://studio.imbue.com/open?git_url=https://github.com/chrisheuer/pairedpunks"><img alt="Open in Imbue Studio" height="64" src="https://img.shields.io/badge/Open%20in%20Imbue%20Studio-D8D1C0?style=for-the-badge"></a>
</p>

Didn't work? Create a Studio workspace and paste this to your agent:
` /use-template https://github.com/chrisheuer/pairedpunks`

## Why you care

Private shared project spaces for hackathon partners: share AI chats, prompts, replies and files with up to 10 people, kept in sync through a private GitHub project.

At a hackathon every partner ends up with their own AI chats, prompts and generated files, and the good ideas get lost in screenshots and pasted snippets. PairedPunks puts the whole team's chats and files side by side in one private space, so you can read what your partner's agent did and build on it in your own chat.

## How to use it

You and each partner need an Imbue Studio (beta) workspace and a GitHub account. The full guide is also inside the app.

**Everyone, once:**

1. Add this template to your Studio and approve GitHub access when asked. PairedPunks uses it to create the private project and sync it.
2. Open **PairedPunks**. Your PairedPunks name (your GitHub username, like `@mayaok`) is on the start screen and at the top right. Click **Copy** and send it to your partners.

**One person starts the project** (the primary member):

1. Click **Start a project** and give it a name and the event.
2. In **Members**, paste each partner's PairedPunks name and click **Invite** -- up to 10 people in total.
3. Share your first chat or file with the upload button at the top.

**Partners join:** open the **project menu** at the top left, and under **Join** click the project you were invited to.

**Working together:**

- **Your chat** is the first tab; each partner has their own tab, and **Side by side** puts your chat next to theirs.
- The sidebar lists chats, artifacts and files by type, by person, or as the project's folder tree.
- Hover any prompt or reply to **copy** it or **add it to your chat** as a draft.
- **Sync** (the circling arrows) pulls in your partners' latest; it also runs every 2 minutes while the window is open.
- **Clone to my computer** downloads the whole project.

Chats can contain passwords or private details -- only share the ones you are happy for every member to read.

## Ideas for making it yours

- Raise or lower the 10-member cap (`MAX_MEMBERS` in `projects.py`) for a bigger team or a strict pair.
- Add a "Summarize my partner's chat" button that turns a long partner chat into a one-paragraph catch-up.
- Post a note to the team's Slack or Discord channel whenever someone shares a new chat.
- Restyle it for your own event: swap the colours and logos under `static/` for your hackathon's brand.
- Add a "submission" view that gathers the files everyone marks as final into one folder for judging.

## What this is

This repository is a published **Imbue Studio template**: a clean, bootable
snapshot of what an agent built, ready to adapt into your own. It is NOT the
generic workspace template -- it is this specific project.

[`template.md`](template.md) is the full manifest -- what it is, how it
works, what it needs to run, and what to adapt -- with the
machine-readable half (recipe, requirements, and the environment it needs
installed) in [`template.toml`](template.toml).
