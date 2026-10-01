import os

os.environ.setdefault("RAG_DISABLE_MODELS", "1")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("ALLOWED_MODELS", "openrouter/free")

import sys
from pathlib import Path

# Projektroot = Ordner über "tests"
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient
import app as backend


@pytest.fixture(autouse=True)
def reset_limits():
    backend._requests.clear()
    backend._daily_chats.clear()
    yield
    backend._requests.clear()
    backend._daily_chats.clear()


@pytest.fixture
def client():
    with TestClient(backend.app) as test_client:
        yield test_client


@pytest.fixture
def chunks():
    return [
        backend.Chunk(id="de-1", source_id="de", source_title="Berlin", text="Berlin ist die Hauptstadt Deutschlands.", locator={"chunk_id": "de-1"}),
        backend.Chunk(id="en-1", source_id="en", source_title="Python", text="Python was created by Guido van Rossum.", locator={"chunk_id": "en-1"}),
        backend.Chunk(id="de-2", source_id="de", source_title="Rhein", text="Der Rhein mündet in die Nordsee.", locator={"chunk_id": "de-2"}),
    ]
