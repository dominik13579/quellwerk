import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import httpx

ROOT = Path(__file__).resolve().parents[2]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server_url():
    port = _free_port()
    env = {**os.environ, "RAG_DISABLE_MODELS": "1", "OPENROUTER_API_KEY": "e2e-placeholder"}
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{url}/api/health", timeout=0.25).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("test server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)
