# Container for running the desk on a server (Phase 6) or anywhere with Docker.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install ".[databento]"
VOLUME ["/app/data", "/app/reports", "/app/logs"]
ENTRYPOINT ["desk"]
CMD ["--help"]
