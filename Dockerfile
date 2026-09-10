FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    SESSION_DB_PATH=/data/travel-agent.db \
    RATE_LIMIT_PER_MINUTE=20

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --create-home app

COPY pyproject.toml ./
COPY *.py ./
COPY web ./web
COPY knowledge ./knowledge

RUN pip install --no-cache-dir . && mkdir -p /data && chown app:app /data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "main.py"]
