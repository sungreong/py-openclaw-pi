FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-piagent.lock.txt requirements-piagent-documents.txt /tmp/
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements-piagent.lock.txt \
    && python -m pip install -r /tmp/requirements-piagent-documents.txt

# Keep the image runnable without the development bind mount. Sensitive and
# generated paths are excluded by .dockerignore.
COPY . /app
