FROM python:3.11.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINDSPACE_HOST=0.0.0.0 \
    MINDSPACE_PORT=8765 \
    MINDSPACE_RUNTIME_DIR=/data

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.26 \
    && uv sync --frozen --no-dev

VOLUME ["/data"]
EXPOSE 8765
CMD [".venv/bin/mindspace-server"]
