# Multi-arch by construction: the python base image manifest covers arm64
# (the Pi 5) and amd64 (dev machines). Pins per D19.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.12-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/

# Non-root; /data is the SQLite volume mount point (compose maps a named
# volume here, which inherits this ownership on first use).
RUN groupadd -r pihome && useradd -r -g pihome -m -d /app pihome \
    && mkdir /data && chown pihome:pihome /data

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependency layer first so code edits don't re-resolve.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev && chown -R pihome:pihome /app

USER pihome
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8100
CMD ["python", "-m", "pihome", "serve"]
