import json
from unittest.mock import patch

import httpx
import pytest
import respx


@pytest.mark.integration
@respx.mock
def test_scrape_endpoint_uses_mocked_website(client):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/article").mock(return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Research</title><main><h1>Topic</h1><p>Verified fact.</p></main></html>"))
    with patch("app.resolve_public_ips", return_value=["93.184.216.34"]):
        response = client.post("/api/scrape", json={"url": "https://example.com/article"})
    assert response.status_code == 200
    assert response.json()["title"] == "Research"
    assert response.json()["text"] == "Verified fact."


@pytest.mark.integration
@respx.mock
def test_chat_streams_mocked_openrouter(client):
    stream = "\n".join([
        'data: {"choices":[{"delta":{"content":"Berlin "}}]}',
        'data: {"choices":[{"delta":{"content":"ist die Hauptstadt [1]."}}]}',
        "data: [DONE]",
        "",
    ])
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream.encode()))
    payload = {
        "query": "Was ist die Hauptstadt?",
        "chunks": [{"id": "c1", "source_id": "s1", "source_title": "Fakten", "text": "Berlin ist die Hauptstadt Deutschlands.", "locator": {"chunk_id": "c1"}}],
        "top_k": 1,
        "history": [],
        "model": "openrouter/free"
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    assert "event: meta" in response.text
    assert "Berlin" in response.text
    assert "event: done" in response.text
    sent = json.loads(route.calls[0].request.content)
    assert sent["max_tokens"] > 0
    assert sent["model"] == "openrouter/free"


@pytest.mark.integration
def test_chat_rejects_unapproved_model(client):
    response = client.post("/api/chat", json={"query": "x", "chunks": [{"id": "c", "source_id": "s", "source_title": "S", "text": "x", "locator": {}}], "model": "paid/expensive"})
    assert response.status_code == 400

@pytest.mark.integration
def test_health_and_config(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    config = client.get("/api/config")
    assert config.status_code == 200
    assert "openrouter/free" in config.json()["allowed_models"]


@pytest.mark.integration
def test_retrieve_endpoint(client):
    response = client.post("/api/retrieve", json={
        "query": "Hauptstadt Berlin",
        "chunks": [
            {"id": "a", "source_id": "s", "source_title": "Fakten", "text": "Berlin ist die Hauptstadt Deutschlands.", "locator": {}},
            {"id": "b", "source_id": "s", "source_title": "Fakten", "text": "Der Rhein fließt in die Nordsee.", "locator": {}},
        ],
        "top_k": 1,
    })
    assert response.status_code == 200
    assert response.json()["hits"][0]["id"] == "a"


@pytest.mark.integration
def test_demo_notebook_disabled_by_default(client):
    response = client.get("/api/demo-notebook")
    assert response.status_code == 404


@pytest.mark.integration
@respx.mock
def test_models_endpoint_filters_allowlist(client):
    respx.get("https://openrouter.ai/api/v1/models").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "openrouter/free", "name": "Router"},
        {"id": "expensive/model", "name": "No"},
    ]}))
    response = client.get("/api/models")
    assert response.status_code == 200
    assert [m["id"] for m in response.json()["models"]] == ["openrouter/free"]
