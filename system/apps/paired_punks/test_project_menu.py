"""The project menu opens and closes repeatedly without hanging the page."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Route
from playwright.sync_api import sync_playwright

APP_HTML = Path(__file__).parent / "src" / "paired_punks" / "app.html"
FORTRESS = "/opt/fortress/tilion-fortress/tilion"
PROJECT = {"slug": "plant-pal", "repo": "ava/pp-plant-pal", "name": "Plant Pal", "event": "", "last_sync": None}
STUBS = {
    "/api/me": {"login": "ava"},
    "/api/projects": {"projects": [PROJECT]},
    "/api/invitations": {"invitations": [{"repo": "bo/pp-kite", "from": "bo", "pending": True, "invitation_id": 7}]},
    "/api/projects/plant-pal/state": {"chats": [], "artifacts": [], "files": [], "tree": [], "local_path": "/x", "shared_chats": []},
    "/api/projects/plant-pal/members": {"members": []},
    "/api/projects/plant-pal/sync": PROJECT,
}


def _serve(route: Route) -> None:
    path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
    if path == "/":
        route.fulfill(body=APP_HTML.read_text(), content_type="text/html")
    elif path in STUBS:
        route.fulfill(body=json.dumps(STUBS[path]), content_type="application/json")
    else:
        route.fulfill(status=404, body="")


@pytest.mark.browser
def test_project_menu_reopens_without_hanging() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=FORTRESS)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route("http://pp.test/**", _serve)
        page.goto("http://pp.test/")
        page.wait_for_selector("aside")
        for _ in range(3):
            page.click("#proj-btn")
            page.wait_for_selector("#menu.open")
            # Fresh invitations land in the open menu, under Join.
            page.wait_for_selector("#menu .mi:has-text('kite')")
            page.click("header .logo")
            page.wait_for_selector("#menu:not(.open)", state="attached")
        browser.close()
