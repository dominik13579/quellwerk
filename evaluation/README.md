# RAG evaluation

The corpus contains fictional facts in German and English so the gold answers are unambiguous and no private or copyrighted documents are redistributed. Every question and relevant chunk was checked manually.

## Retrieval run

```bash
RAG_DISABLE_MODELS=1 python evaluation/run.py
cat evaluation/results/metrics.json
```

This produces Recall@1/3/5, MRR, mean/P95 retrieval latency and retrieval error rate. With `RAG_DISABLE_MODELS=1`, the baseline measures BM25 only and is deterministic.

For the full configured pipeline, remove `RAG_DISABLE_MODELS=1`. The first run downloads embedding/reranker models and is therefore not representative for warm latency.

## End-to-end answer run

Start Quellwerk with a valid provider key, then run:

```bash
python evaluation/run.py --live-url http://localhost:8000 --model openrouter/free
```

The script records answer latency and error rate. It also reports `citation_precision_auto` and a `groundedness_proxy`. The proxy checks citation coverage and correct abstention only; it is **not** a semantic entailment score and must not be presented as human groundedness.

## Human review

After a live run, open `evaluation/results/human_review.csv`. For every answer, fill:

- `citation_precision_human_0_to_1`: supported citations divided by all citations.
- `groundedness_human_0_to_1`: supported factual claims divided by all factual claims.
- `answer_correct_0_or_1`: whether the answer contains all expected facts or correctly abstains.
- `review_notes`: short explanation for failures.

Then run:

```bash
python evaluation/summarize_review.py
```

Commit a dated, reviewed `human_metrics.json` and a short methodology note if the results are shown in the portfolio. Do not commit raw provider responses if they contain user documents.
