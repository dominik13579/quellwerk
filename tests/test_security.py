import socket
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi import HTTPException

import app

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "http://user:pass@example.com/",
    "http://example.com:8080/",
    "http://example.com\\@127.0.0.1/",
])
def test_validate_public_url_rejects_unsafe_shapes(url):
    with pytest.raises(HTTPException):
        app.validate_public_url(url)


@pytest.mark.unit
def test_validate_public_url_accepts_public_https():
    with patch("socket.getaddrinfo", return_value=PUBLIC_DNS):
        assert app.validate_public_url("https://example.com/research") == "https://example.com/research"


@pytest.mark.unit
def test_validate_public_url_rejects_private_ip():
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
    with patch("socket.getaddrinfo", return_value=private), pytest.raises(HTTPException) as exc:
        app.validate_public_url("http://internal.example/")
    assert exc.value.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
@respx.mock
async def test_fetch_public_html_follows_validated_redirects():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/start").mock(return_value=httpx.Response(302, headers={"location": "/final"}))
    respx.get("https://example.com/final").mock(return_value=httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text="<main><p>Evidence</p></main>"))
    with patch("app.resolve_public_ips", return_value=["93.184.216.34"]):
        url, response = await app.fetch_public_html("https://example.com/start")
    assert url == "https://example.com/final"
    assert "Evidence" in response.text


@pytest.mark.integration
@pytest.mark.asyncio
@respx.mock
async def test_fetch_public_html_revalidates_redirect_target():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/start").mock(return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}))
    def dns(host, port, **kwargs):
        ip = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    with patch("socket.getaddrinfo", side_effect=dns):
        with pytest.raises(HTTPException) as exc:
            await app.fetch_public_html("https://example.com/start")
    assert exc.value.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
@respx.mock
async def test_fetch_public_html_rejects_non_html():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/file").mock(return_value=httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"binary"))
    with patch("app.resolve_public_ips", return_value=["93.184.216.34"]), pytest.raises(HTTPException) as exc:
        await app.fetch_public_html("https://example.com/file")
    assert exc.value.status_code == 415
