FROM python:3.12.10-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --system quellwerk && useradd --system --gid quellwerk --home /app quellwerk
COPY pyproject.toml README.md app.py ./
RUN pip install --upgrade pip==25.1.1 && pip install .
COPY quellwerk.html ./
COPY static ./static
COPY demo ./demo
RUN chown -R quellwerk:quellwerk /app
USER quellwerk
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
