FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINDSPACE_HOST=0.0.0.0 \
    MINDSPACE_PORT=8765 \
    MINDSPACE_RUNTIME_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8765
CMD ["mindspace-server"]
