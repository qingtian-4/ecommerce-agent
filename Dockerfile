FROM ghcr.io/astral-sh/uv:0.11.8 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN uv sync --frozen --no-dev --no-editable
ENV ACS_DB_PATH=/app/persist/business.db \
    ACS_INDEX_DIR=/app/persist/index \
    ACS_TRACE_DIR=/app/persist/traces \
    PYTHONUNBUFFERED=1
VOLUME ["/app/persist"]
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["/app/.venv/bin/python", "scripts/check_runtime.py", "--url", "http://127.0.0.1:7860/"]
ENTRYPOINT ["/app/.venv/bin/python", "scripts/serve.py"]
CMD ["--host", "0.0.0.0", "--port", "7860"]
