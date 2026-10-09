"""The privacy warning, partner alerts and catch-up summaries, driven in a browser against stubbed GitHub data."""

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page
from playwright.sync_api import Route
from playwright.sync_api import sync_playwright

APP_HTML = Path(__file__).parent / "src" / "paired_punks" / "app.html"
FORTRESS = "/opt/fortress/tilion-fortress/tilion"
PROJECT = {"slug": "plant-pal", "repo": "ava/pp-plant-pal", "name": "Plant Pal", "event": "", "last_sync": None}
API = "/api/projects/plant-pal/"


def _chat(path: str, title: str, last: str, turns: int) -> dict[str, Any]:
    return {"id": path.split("/")[-1][:-5], "title": title, "author": "bo", "path": path, "last_activity": last,
            "live": False, "turns": turns}


class FakeServer:
    """The app's API with the data each test sets, recording what the page asked for."""

    def __init__(self) -> None:
        self.project = dict(PROJECT, private=True, can_change_visibility=True)
        self.chats: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def state(self) -> dict[str, Any]:
        return {"chats": self.chats, "artifacts": [], "files": [], "tree": [], "local_path": "/x", "shared_chats": []}

    def serve(self, route: Route) -> None:
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        self.calls.append(path)
        replies: dict[str, Any] = {
            "/api/me": {"login": "ava"},
            "/api/projects": {"projects": [self.project]},
            "/api/invitations": {"invitations": []},
            API + "state": self.state(),
            API + "members": {"members": [{"login": "ava", "owner": True, "pending": False},
                                          {"login": "bo", "owner": False, "pending": False}]},
            API + "sync": self.project,
            API + "chat": {"title": "Voice notes", "author": "bo", "turns": [{"role": "prompt", "text": "Build it"}]},
            API + "catch-up": {"summary": "Bo wired up the voice recorder.", "cached": False, "cost_usd": 0.03},
        }
        if path == API + "make-private":
            self.project = dict(self.project, private=True)
            replies[path] = self.project
        if path == "/":
            route.fulfill(body=APP_HTML.read_text(), content_type="text/html")
        elif path in replies:
            route.fulfill(body=json.dumps(replies[path]), content_type="application/json")
        else:
            route.fulfill(status=404, body="")


def _open(page: Page, server: FakeServer) -> None:
    page.set_default_timeout(5000)
    page.route("http://pp.test/**", server.serve)
    page.goto("http://pp.test/")
    page.wait_for_selector("aside")


def _sync(page: Page) -> None:
    page.evaluate("syncNow(false)")
    page.wait_for_function("!S.syncing")


@pytest.mark.browser
def test_a_public_project_shows_a_warning_that_makes_it_private() -> None:
    server = FakeServer()
    server.project["private"] = False
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=FORTRESS)
        page = browser.new_page()
        _open(page, server)
        page.wait_for_selector("#privacy-warn:has-text('public on GitHub')")
        page.click("#make-private")
        page.wait_for_selector("#privacy-warn", state="hidden")
        assert API + "make-private" in server.calls
        browser.close()


@pytest.mark.browser
def test_a_partner_who_cannot_change_it_is_told_who_can() -> None:
    server = FakeServer()
    server.project.update(private=False, can_change_visibility=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=FORTRESS)
        page = browser.new_page()
        _open(page, server)
        page.wait_for_selector("#privacy-warn:has-text('Ask ava')")
        assert page.locator("#make-private").count() == 0
        browser.close()


@pytest.mark.browser
def test_partner_chats_alert_once_new_and_again_when_added_to() -> None:
    server = FakeServer()
    server.chats = [_chat("chats/bo/old.json", "Old idea", "2026-10-09T01:00:00+00:00", 2)]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=FORTRESS)
        page = browser.new_page()
        _open(page, server)
        # What was already shared on the first visit is not news.
        assert page.locator(".alert").count() == 0
        server.chats.append(_chat("chats/bo/voice.json", "Voice notes", "2026-10-09T02:00:00+00:00", 2))
        _sync(page)
        page.wait_for_selector(".alert:has-text('bo shared a chat'):has-text('Voice notes')")
        assert page.title() == "(1) PairedPunks"
        page.click(".alert button:has-text('Open')")
        page.wait_for_selector(".alert", state="detached")
        assert page.title() == "PairedPunks"
        # Seen stays seen across a reload; a new message in the chat alerts again.
        page.reload()
        page.wait_for_selector("aside")
        assert page.locator(".alert").count() == 0
        server.chats[1] = _chat("chats/bo/voice.json", "Voice notes", "2026-10-09T03:00:00+00:00", 4)
        _sync(page)
        page.wait_for_selector(".alert:has-text('bo added to a chat')")
        browser.close()


@pytest.mark.browser
def test_catch_me_up_shows_the_summary_above_the_chat() -> None:
    server = FakeServer()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=FORTRESS)
        page = browser.new_page()
        _open(page, server)
        server.chats = [_chat("chats/bo/voice.json", "Voice notes", "2026-10-09T02:00:00+00:00", 2)]
        _sync(page)
        page.click(".alert button:has-text('Catch me up')")
        page.wait_for_selector(".catchup:has-text('Bo wired up the voice recorder.')")
        # The chat itself is still right below the summary.
        page.wait_for_selector(".thread .msg.prompt:has-text('Build it')")
        browser.close()
