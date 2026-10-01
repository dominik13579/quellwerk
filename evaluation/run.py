from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("RAG_DISABLE_MODELS", "1")
from app import Chunk, retrieve

DATA = ROOT / "data"
RESULTS = ROOT / "results"


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def retrieval_metrics(rows, ks=(1, 3, 5)):
    answerable = [r for r in rows if r["answerable"]]
    metrics = {}
    for k in ks:
        metrics[f"recall@{k}"] = sum(bool(set(r["retrieved_ids"][:k]) & set(r["relevant_ids"])) for r in answerable) / len(answerable)
    reciprocals = []
    for row in answerable:
        ranks = [i for i, value in enumerate(row["retrieved_ids"], 1) if value in row["relevant_ids"]]
        reciprocals.append(1 / min(ranks) if ranks else 0)
    metrics["mrr"] = statistics.mean(reciprocals)
    metrics["retrieval_error_rate"] = sum(bool(r["error"]) for r in rows) / len(rows)
    metrics["retrieval_latency_ms_mean"] = statistics.mean(r["retrieval_latency_ms"] for r in rows)
    metrics["retrieval_latency_ms_p95"] = sorted(r["retrieval_latency_ms"] for r in rows)[max(0, int(len(rows) * 0.95) - 1)]
    return metrics


def parse_sse(text: str):
    answer, hits, error = "", [], ""
    for block in text.split("\n\n"):
        event, data = "message", None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event == "meta" and data:
            hits = data.get("hits", [])
        elif event == "token" and data:
            answer += data.get("text", "")
        elif event == "error" and data:
            error = data.get("message", "generation error")
    return answer, hits, error


def auto_generation_metrics(rows):
    completed = [r for r in rows if r.get("generation_attempted")]
    if not completed:
        return {}
    precision_values, groundedness_proxies = [], []
    for row in completed:
        refs = [int(x) for x in re.findall(r"\[(\d+)\]", row.get("answer", ""))]
        hit_ids = row.get("generation_hit_ids", [])
        cited_ids = [hit_ids[n - 1] for n in refs if 0 < n <= len(hit_ids)]
        if row["answerable"]:
            precision_values.append(sum(cid in row["relevant_ids"] for cid in cited_ids) / len(cited_ids) if cited_ids else 0)
            sentences = [x for x in re.split(r"(?<=[.!?])\s+", row.get("answer", "").strip()) if x]
            groundedness_proxies.append(sum(bool(re.search(r"\[\d+\]", s)) for s in sentences) / len(sentences) if sentences else 0)
        else:
            abstains = any(term in row.get("answer", "").lower() for term in ["nicht beantwortbar", "keine information", "cannot be answered", "not provided", "not in the sources"])
            groundedness_proxies.append(float(abstains))
    return {
        "citation_precision_auto": statistics.mean(precision_values) if precision_values else 0,
        "groundedness_proxy": statistics.mean(groundedness_proxies) if groundedness_proxies else 0,
        "answer_latency_ms_mean": statistics.mean(r["answer_latency_ms"] for r in completed),
        "answer_error_rate": sum(bool(r.get("generation_error")) for r in completed) / len(completed),
        "note": "Groundedness proxy measures citation coverage/abstention, not semantic entailment. Use human_review.csv for the portfolio metric."
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-url", help="Running Quellwerk URL, e.g. http://localhost:8000")
    parser.add_argument("--model", default="openrouter/free")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    corpus = read_jsonl(DATA / "corpus.jsonl")
    questions = read_jsonl(DATA / "questions.jsonl")
    chunks = [Chunk(id=d["id"], source_id=d["source_id"], source_title=d["source_title"], text=d["text"], locator=d["locator"]) for d in corpus]
    rows = []
    for q in questions:
        started = time.perf_counter()
        error = ""
        try:
            hits = retrieve(q["question"], chunks, args.top_k)
        except Exception as exc:
            hits, error = [], str(exc)
        latency = (time.perf_counter() - started) * 1000
        row = {
            "question_id": q["id"], "language": q["language"], "question": q["question"],
            "answerable": q["answerable"], "relevant_ids": q["relevant_chunk_ids"],
            "retrieved_ids": [h["id"] for h in hits], "retrieval_latency_ms": round(latency, 3), "error": error,
            "generation_attempted": False, "answer": "", "generation_hit_ids": [], "answer_latency_ms": 0, "generation_error": "",
        }
        if args.live_url:
            row["generation_attempted"] = True
            payload = {"query": q["question"], "chunks": [c.model_dump() for c in chunks], "top_k": args.top_k, "history": [], "model": args.model}
            started = time.perf_counter()
            try:
                response = httpx.post(f"{args.live_url.rstrip('/')}/api/chat", json=payload, timeout=120)
                response.raise_for_status()
                answer, answer_hits, generation_error = parse_sse(response.text)
                row.update(answer=answer, generation_hit_ids=[h["id"] for h in answer_hits], generation_error=generation_error)
            except Exception as exc:
                row["generation_error"] = str(exc)
            row["answer_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        rows.append(row)
    RESULTS.mkdir(parents=True, exist_ok=True)
    metrics = retrieval_metrics(rows)
    metrics.update(auto_generation_metrics(rows))
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    with (RESULTS / "details.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "relevant_ids": "|".join(row["relevant_ids"]), "retrieved_ids": "|".join(row["retrieved_ids"]), "generation_hit_ids": "|".join(row["generation_hit_ids"])})
    with (RESULTS / "human_review.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["question_id", "citation_precision_human_0_to_1", "groundedness_human_0_to_1", "answer_correct_0_or_1", "review_notes"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({"question_id": row["question_id"]})
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
