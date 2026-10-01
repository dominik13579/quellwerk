import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.e2e
def test_import_question_and_citation_click(page: Page, live_server_url):
    page.route("**/api/health", lambda route: route.fulfill(json={"ok": True, "ai_configured": True}))
    page.route("**/api/config", lambda route: route.fulfill(json={"demo_mode": False, "allow_user_sources": True, "allowed_models": ["openrouter/free"]}))
    page.route("**/api/models", lambda route: route.fulfill(json={"models": [{"id": "openrouter/free", "name": "Free Models Router"}]}))
    page.route("**/api/chat", lambda route: route.fulfill(status=200, content_type="text/event-stream", body='event: meta\ndata: {"hits":[{"id":"1-c0","source_id":"1","source_title":"Berlin Fakten","text":"Berlin ist die Hauptstadt Deutschlands.","locator":{"type":"text","start":0,"end":41,"label":"Absatz 1"},"rank":1,"score":0.1}]}\n\nevent: token\ndata: {"text":"Berlin ist die Hauptstadt Deutschlands [1]."}\n\nevent: done\ndata: {}\n\n'))

    page.goto(live_server_url)
    page.get_by_role("button", name="Quelle hinzufügen").click()
    page.get_by_role("button", name=re.compile("Text.*Text einfügen", re.S)).click()
    page.locator("#textTitle").fill("Berlin Fakten")
    page.locator("#textBody").fill("Berlin ist die Hauptstadt Deutschlands.")
    page.locator("#saveSource").click()
    expect(page.get_by_text("Berlin Fakten", exact=True).first).to_be_visible()

    page.locator("#chatInput").fill("Was ist die Hauptstadt Deutschlands?")
    page.locator("#chatForm").evaluate("form => form.requestSubmit()")
    citation = page.locator("button.cite").first
    expect(citation).to_be_visible()
    citation.click()
    expect(page.locator(".source-passage.highlight")).to_contain_text("Berlin ist die Hauptstadt")
