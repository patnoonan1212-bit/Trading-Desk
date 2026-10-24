# Container for running the desk on a server (`desk run`: one update every minute), or any one-off command.
# Keys and settings come from environment variables (the same names as the GitHub secrets); never bake them in.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY learning ./learning
RUN pip install ".[databento,live,ai,tradovate]"
# Paper account, prices and logs live here; mount a persistent volume so they survive restarts.
VOLUME ["/app/paper-state", "/app/data", "/app/logs"]
ENTRYPOINT ["desk"]
CMD ["run", "--every", "60"]
