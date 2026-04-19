FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps kept minimal — pandas/numpy wheels are available on slim Python.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install everything except playwright (it's only used by the CLI PNG/PDF export,
# which isn't wired to the web app).  This keeps the image small.
RUN grep -v '^playwright' requirements.txt > /tmp/req.txt \
    && pip install -r /tmp/req.txt

COPY src ./src
COPY templates ./templates
COPY assets ./assets
COPY config ./config

# SQLite file lives on the Render persistent disk mounted at /var/data.
ENV DB_PATH=/var/data/app.db
RUN apt-get purge -y build-essential && apt-get autoremove -y

EXPOSE 8000
CMD ["sh", "-c", "uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
