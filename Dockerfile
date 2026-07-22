FROM node:22-slim AS node-runtime

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
RUN node --version

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import cv2, numpy; print('cv2 import ok', cv2.__version__)"

COPY . .
