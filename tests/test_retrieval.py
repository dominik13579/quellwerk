import pytest
import app


@pytest.mark.unit
def test_split_with_offsets_covers_text_and_overlaps():
    text = "Erster Satz. " * 80
    chunks = app.split_with_offsets(text, target=120, overlap=20)
    assert len(chunks) > 2
    assert chunks[0]["start"] == 0
    assert chunks[-1]["end"] == len(text)
    assert chunks[1]["start"] < chunks[0]["end"]
    assert all(chunk["text"] for chunk in chunks)


@pytest.mark.unit
def test_split_with_offsets_rejects_invalid_config():
    with pytest.raises(ValueError):
        app.split_with_offsets("text", target=100, overlap=100)


@pytest.mark.unit
def test_reciprocal_rank_fusion_rewards_consensus():
    scores = app.reciprocal_rank_fusion([[0, 1, 2], [1, 0, 2]], k=60)
    assert scores[0] == scores[1]
    assert scores[0] > scores[2]


@pytest.mark.unit
def test_retrieve_bm25_returns_relevant_chunk_first(monkeypatch, chunks):
    monkeypatch.setattr(app, "load_models", lambda: (None, None))
    hits = app.retrieve("Wer ist die Hauptstadt Deutschlands Berlin?", chunks, top_k=2)
    assert hits[0]["id"] == "de-1"
    assert [hit["rank"] for hit in hits] == [1, 2]


@pytest.mark.unit
def test_retrieve_empty():
    assert app.retrieve("anything", [], top_k=5) == []
