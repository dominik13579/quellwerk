# Contributing

1. Fork the repository and create a focused branch from `main`.
2. Install development dependencies with `python -m pip install -e '.[test]'`.
3. Run `python -m playwright install chromium` once.
4. Add or update tests for every behavior change.
5. Run `pytest -m 'not e2e and not live'` and `pytest -m e2e` before opening a PR.
6. Never add API keys, source documents containing personal data, or `.env` files.
7. Keep pull requests small and explain security, privacy, retrieval-quality and UX trade-offs.
