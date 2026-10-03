"""Quellwerk MVP backend: secure imports, hybrid RAG and streamed OpenRouter chat."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from dotenv import load_dotenv
load_dotenv()

import httpx
import numpy as np
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl
from rank_bm25 import BM25Okapi
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.robotparser import RobotFileParser

CRAWLER_TOKEN = "QuellwerkBot"
CRAWLER_USER_AGENT = (
    "QuellwerkBot/3.1 "
    "(personal research assistant; contact: deine-email@example.com)"
)

ROOT = Path(__file__).resolve().parent
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_IMPORT_BYTES = int(os.getenv("MAX_IMPORT_BYTES", "5000000"))
MAX_REDIRECTS = int(os.getenv("MAX_REDIRECTS", "3"))
MAX_CHAT_CHARS = int(os.getenv("MAX_CHAT_CHARS", "2000000"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "800"))
DAILY_CHAT_LIMIT = int(os.getenv("DAILY_CHAT_LIMIT", "200"))
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"
ALLOW_USER_SOURCES = os.getenv("ALLOW_USER_SOURCES", "1") == "1"
ALLOWED_MODELS = {x.strip() for x in os.getenv("ALLOWED_MODELS", "openrouter/free").split(",") if x.strip()}
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()]

app = FastAPI(title="Quellwerk API", version="3.1")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

_requests: dict[str, deque[float]] = defaultdict(deque)
_daily_chats: dict[str, tuple[str, int]] = {}
# _embedder: Any = None
# _reranker: Any = None


class UrlRequest(BaseModel):
    url: HttpUrl


class Chunk(BaseModel):
    id: str
    source_id: str
    source_title: str
    text: str = Field(min_length=1, max_length=8000)
    locator: dict[str, Any] = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    chunks: list[Chunk] = Field(max_length=5000)
    top_k: int = Field(8, ge=1, le=20)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=12000)


class ChatRequest(RetrieveRequest):
    model: str = "openrouter/free"
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


def rate_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _requests[key]
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(429, "Zu viele Anfragen. Bitte kurz warten.")
    bucket.append(now)


def resolve_public_ips(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(400, "Hostname konnte nicht aufgelöst werden") from exc
    addresses = sorted({item[4][0].split("%", 1)[0] for item in infos})
    if not addresses:
        raise HTTPException(400, "Hostname hat keine A/AAAA-Adresse")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise HTTPException(400, "Ungültige Zieladresse") from exc
        if not ip.is_global:
            raise HTTPException(400, f"Nicht öffentliche Zieladresse blockiert: {ip}")
    return addresses


def validate_public_url(value: str) -> str:
    if "\\" in value:
        raise HTTPException(400, "Backslashes in URLs sind nicht erlaubt")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, "Nur HTTP/HTTPS-URLs sind erlaubt")
    if parsed.username or parsed.password:
        raise HTTPException(400, "Zugangsdaten in URLs sind nicht erlaubt")
    if not parsed.hostname:
        raise HTTPException(400, "Die URL enthält keinen Hostnamen")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise HTTPException(400, "Ungültiger Port") from exc
    if port not in {80, 443}:
        raise HTTPException(400, "Nur die Ports 80 und 443 sind erlaubt")
    resolve_public_ips(parsed.hostname, port)
    return value

async def ensure_robots_allowed(
    client: httpx.AsyncClient,
    target_url: str,
) -> None:
    parsed = urlsplit(target_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        response = await client.get(
            robots_url,
            headers={"User-Agent": CRAWLER_USER_AGENT},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            422,
            f"Robot-Policy konnte nicht geprüft werden: {exc}",
        ) from exc

    if response.status_code == 200:
        robots = RobotFileParser()
        robots.set_url(robots_url)
        robots.parse(response.text.splitlines())

        if not robots.can_fetch(CRAWLER_TOKEN, target_url):
            raise HTTPException(
                403,
                "Die Website untersagt den Abruf dieser URL in robots.txt.",
            )

async def fetch_public_html(value: str) -> tuple[str, httpx.Response]:
    current = validate_public_url(value)
    headers = {
        "User-Agent": CRAWLER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
    }
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(20, connect=5),
        headers=headers,
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            await ensure_robots_allowed(client, current)
            async with client.stream("GET", current) as upstream:
                if upstream.status_code in {301, 302, 303, 307, 308}:
                    location = upstream.headers.get("location")
                    if not location:
                        raise HTTPException(422, "Redirect ohne Location-Header")
                    if redirect_count >= MAX_REDIRECTS:
                        raise HTTPException(422, "Zu viele Redirects")
                    current = validate_public_url(urljoin(current, location))
                    continue
                if upstream.status_code == 403:
                    raise HTTPException(403, "Die Website lehnt automatisierte Abrufe ab")
                upstream.raise_for_status()
                content_type = upstream.headers.get("content-type", "").lower()
                if not any(t in content_type for t in ("text/html", "application/xhtml+xml")):
                    raise HTTPException(415, "Nur HTML-Webseiten können importiert werden")
                body = bytearray()
                async for part in upstream.aiter_bytes():
                    body.extend(part)
                    if len(body) > MAX_IMPORT_BYTES:
                        raise HTTPException(413, "Webseite überschreitet das Importlimit")
                
                response_headers = dict(upstream.headers)
                # Doppelte Dekompression verhindern:
                response_headers.pop("content-encoding", None)
                response_headers.pop("Content-Encoding", None)
                response_headers.pop("content-length", None)
                response_headers.pop("Content-Length", None)

                response = httpx.Response(
                    upstream.status_code,
                    headers=response_headers,
                    content=bytes(body),
                    request=upstream.request,
                )
                return current, response
    raise HTTPException(422, "Webseite konnte nicht geladen werden")


def text_sections(soup: BeautifulSoup) -> tuple[str, list[dict[str, Any]]]:
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body
    if not root:
        return "", []
    sections: list[dict[str, Any]] = []
    full: list[str] = []
    offset = 0
    heading = "Inhalt"
    for node in root.find_all(["h1", "h2", "h3", "p", "li", "pre", "blockquote"]):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not text:
            continue
        if node.name in {"h1", "h2", "h3"}:
            heading = text[:160]
            continue
        start = offset
        full.append(text)
        offset += len(text) + 2
        sections.append({"heading": heading, "text": text, "start": start, "end": start + len(text)})
    return "\n\n".join(full), sections


def youtube_id(value: str) -> str:
    patterns = [r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/(?:embed|shorts)/)([\w-]{11})", r"^([\w-]{11})$"]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    raise HTTPException(400, "Ungültige YouTube-URL")


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\wäöüß]{2,}", text.lower())


def split_with_offsets(text: str, target: int = 900, overlap: int = 120) -> list[dict[str, Any]]:
    if target < 1 or overlap < 0 or overlap >= target:
        raise ValueError("target must be positive and overlap smaller than target")
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target)
        if end < len(text):
            boundary = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if boundary > start + target * 0.55:
                end = boundary + 1
        value = text[start:end].strip()
        if value:
            chunks.append({"text": value, "start": start, "end": end})
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


# def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
#     scores: dict[int, float] = defaultdict(float)
#     for ranking in rankings:
#         for rank, index in enumerate(ranking, 1):
#             scores[index] += 1.0 / (k + rank)
#     return scores


# def load_models() -> tuple[Any, Any]:
#     global _embedder, _reranker
#     if os.getenv("RAG_DISABLE_MODELS") == "1":
#         return None, None
#     try:
#         from sentence_transformers import CrossEncoder, SentenceTransformer
#         if _embedder is None:
#             _embedder = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small"))
#         if _reranker is None:
#             _reranker = CrossEncoder(os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"))
#     except Exception:
#         return None, None
#     return _embedder, _reranker


def retrieve(query: str, chunks: list[Chunk], top_k: int) -> list[dict[str, Any]]:
    if not chunks:
        return []
    corpus_tokens = [tokenize(c.text) or ["_"] for c in chunks]
    bm25 = BM25Okapi(corpus_tokens)
    bm_scores = np.asarray(bm25.get_scores(tokenize(query) or ["_"]))

    # Indizes der besten Chunks nach BM25
    ranked_ids = sorted(range(len(chunks)), key=lambda i: bm_scores[i], reverse=True)[:top_k]

    results = []
    for rank, index in enumerate(ranked_ids, 1):
        chunk = chunks[index]
        results.append({
            **chunk.model_dump(),
            "rank": rank,
            "score": float(bm_scores[index]),
        })
    return results

    # bm_rank = np.argsort(-bm_scores).tolist()
    # rankings = [bm_rank]
    # embedder, reranker = load_models()
    # if embedder is not None:
    #     passages = [f"passage: {c.text}" for c in chunks]
    #     doc_vectors = embedder.encode(passages, normalize_embeddings=True, show_progress_bar=False)
    #     query_vector = embedder.encode([f"query: {query}"], normalize_embeddings=True, show_progress_bar=False)[0]
    #     dense_scores = np.asarray(doc_vectors) @ np.asarray(query_vector)
    #     rankings.append(np.argsort(-dense_scores).tolist())
    # fused = reciprocal_rank_fusion(rankings)
    # candidate_ids = sorted(fused, key=fused.get, reverse=True)[: min(30, len(chunks))]
    # if reranker is not None and candidate_ids:
    #     pairs = [(query, chunks[i].text) for i in candidate_ids]
    #     scores = np.asarray(reranker.predict(pairs, show_progress_bar=False))
    #     candidate_ids = [candidate_ids[i] for i in np.argsort(-scores)]
    # results = []
    # for rank, index in enumerate(candidate_ids[:top_k], 1):
    #     chunk = chunks[index]
    #     results.append({**chunk.model_dump(), "rank": rank, "score": round(float(fused[index]), 6)})
    # return results


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "quellwerk", "version": "3.1", "ai_configured": bool(os.getenv("OPENROUTER_API_KEY"))}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "demo_mode": DEMO_MODE,
        "allow_user_sources": ALLOW_USER_SOURCES,
        "allowed_models": sorted(ALLOWED_MODELS),
        "daily_chat_limit": DAILY_CHAT_LIMIT,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }


@app.get("/api/demo-notebook")
def demo_notebook() -> FileResponse:
    if not DEMO_MODE:
        raise HTTPException(404, "Demo-Modus ist nicht aktiv")
    return FileResponse(ROOT / "demo" / "notebook.json", media_type="application/json")


@app.get("/api/models")
async def models(request: Request) -> dict[str, Any]:
    rate_limit(request)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("https://openrouter.ai/api/v1/models")
    response.raise_for_status()
    data = response.json().get("data", [])
    visible = [
        {"id": m["id"], "name": m.get("name", m["id"])}
        for m in data
        if m.get("id") in ALLOWED_MODELS
    ]
    defaults = [{"id": model, "name": "Free Models Router" if model == "openrouter/free" else model} for model in sorted(ALLOWED_MODELS)]
    by_id = {item["id"]: item for item in [*defaults, *visible]}
    return {"models": list(by_id.values())}


@app.post("/api/scrape")
async def scrape(req: UrlRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    try:
        final_url, response = await fetch_public_html(str(req.url))
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else final_url
        text, sections = text_sections(soup)
        if not text:
            raise HTTPException(422, "Kein lesbarer Text gefunden")
        return {"title": title, "url": final_url, "text": text, "sections": sections}
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(422, f"Webseite konnte nicht gelesen werden: {exc}") from exc


@app.post("/api/youtube")
async def youtube(req: UrlRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    video_id = youtube_id(str(req.url))
    try:
        api = YouTubeTranscriptApi()
        transcript_list = await asyncio.to_thread(api.list, video_id)
        try:
            transcript = transcript_list.find_transcript(["de", "de-DE", "en", "en-US", "en-GB"])
        except Exception:
            transcript = next(iter(transcript_list))
        fetched = await asyncio.to_thread(transcript.fetch)
        segments = [{"text": s.text.strip(), "start": float(s.start), "duration": float(s.duration)} for s in fetched if s.text.strip()]
        text = " ".join(s["text"] for s in segments)
        title = f"YouTube · {video_id}"
        async with httpx.AsyncClient(timeout=10) as client:
            meta = await client.get("https://www.youtube.com/oembed", params={"url": str(req.url), "format": "json"})
            if meta.is_success:
                title = meta.json().get("title", title)
        return {"video_id": video_id, "title": title, "language": transcript.language, "text": text, "segments": segments}
    except Exception as exc:
        raise HTTPException(422, f"Transkript nicht verfügbar: {exc}") from exc


@app.post("/api/retrieve")
def retrieve_api(req: RetrieveRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    return {"hits": retrieve(req.query, req.chunks, req.top_k)}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    rate_limit(request)
    if req.model not in ALLOWED_MODELS:
        raise HTTPException(400, "Dieses Modell ist serverseitig nicht freigegeben")
    total_chars = sum(len(chunk.text) for chunk in req.chunks)
    if total_chars > MAX_CHAT_CHARS:
        raise HTTPException(413, "Der ausgewählte Quellenkontext ist zu groß")
    client_key = request.client.host if request.client else "unknown"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    saved_day, count = _daily_chats.get(client_key, (day, 0))
    if saved_day != day:
        count = 0
    if count >= DAILY_CHAT_LIMIT:
        raise HTTPException(429, "Tageslimit der Demo erreicht")
    _daily_chats[client_key] = (day, count + 1)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(503, "OPENROUTER_API_KEY ist im Backend nicht gesetzt")
    hits = await asyncio.to_thread(retrieve, req.query, req.chunks, req.top_k)
    context = "\n\n---\n\n".join(
        f"[{i}] {h['source_title']} | {json.dumps(h['locator'], ensure_ascii=False)}\n{h['text']}" for i, h in enumerate(hits, 1)
    )
    system = (
        "Du bist Quellwerk. Antworte auf Deutsch ausschließlich anhand der Fundstellen. "
        "Wenn die Frage anhand der Fundstellen nicht beantwortbar ist, sage kurz und klar, "
        "dass sie aus den Quellen nicht beantwortet werden kann. "
        "Gib in diesem Fall keine Vermutungen oder externes Wissen wieder. "
        "Setze nach jeder belegbaren Tatsachenbehauptung eine Fundstelle wie [1] und verwende nur vorhandene Nummern. "
        "Gib NIEMALS Moderations- oder Sicherheitslabels wie 'User Safety: safe' oder ähnliche Systemhinweise aus. "
        "Gib außerdem NIEMALS Abschnitte mit Überschriften wie 'Here is a thinking process', "
        "'Here’s a thinking process', 'Reasoning', 'Analysis' oder nummerierte Denk-Schritte aus. "
        "Formuliere ausschließlich die fertige Antwort für den Nutzer.\n\n"
        f"FUNDSTELLEN:\n{context}"
    )

    # messages = [{"role": "system", "content": system}]
    # messages.extend(m.model_dump() for m in req.history[-12:])
    # messages.append({"role": "user", "content": req.query})

    messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": req.query},
    ]

    async def stream():
        yield sse("meta", {"hits": hits})
        payload = {"model": req.model, "messages": messages, "temperature": 0.2, "max_tokens": MAX_OUTPUT_TOKENS, "stream": True}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "Quellwerk"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
                async with client.stream("POST", OPENROUTER_URL, headers=headers, json=payload) as response:
                    if not response.is_success:
                        body = (await response.aread()).decode(errors="replace")[:1000]
                        yield sse("error", {"message": f"OpenRouter HTTP {response.status_code}: {body}"})
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            token = json.loads(raw).get("choices", [{}])[0].get("delta", {}).get("content")
                        except (json.JSONDecodeError, IndexError, AttributeError):
                            token = None
                        if token:
                            yield sse("token", {"text": token})
            yield sse("done", {})
        except (httpx.HTTPError, asyncio.CancelledError) as exc:
            if not isinstance(exc, asyncio.CancelledError):
                yield sse("error", {"message": f"Streaming fehlgeschlagen: {exc}"})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "quellwerk.html")
