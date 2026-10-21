FROM denoland/deno:bin-2.9.7@sha256:bc5aa4466e21b6d3021226a85ba2e1911f7c386254d97b9d797903ab74edace2 AS deno-runtime

FROM python:3.11.17-slim-trixie@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8 AS ffmpeg-runtime
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential pkg-config nasm curl ca-certificates xz-utils \
    libgnutls28-dev libmp3lame-dev libopus-dev libvorbis-dev libx264-dev \
    libvpx-dev libxml2-dev libass-dev libfreetype6-dev libfontconfig1-dev libdav1d-dev
COPY scripts/install_ffmpeg_runtime.sh /tmp/install_ffmpeg_runtime.sh
RUN sh /tmp/install_ffmpeg_runtime.sh

FROM python:3.11.17-slim-trixie@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DENO_TLS_CA_STORE=system

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
       libgnutls30t64 libmp3lame0 libopus0 libvorbis0a libvorbisenc2 libx264-164 \
       libvpx9 libxml2 libass9 libfreetype6 libfontconfig1 libdav1d7 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419+deb13u1' \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deno-runtime /deno /usr/local/bin/deno
COPY --from=ffmpeg-runtime /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
RUN ffmpeg -version && ffprobe -version
COPY scripts/install_node_runtime.sh /tmp/install_node_runtime.sh
RUN sh /tmp/install_node_runtime.sh && rm /tmp/install_node_runtime.sh && deno --version

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 setuptools==83.0.0 \
    && python -m pip install --no-cache-dir --require-hashes -r requirements.txt
RUN python -c "import cv2, numpy; print('cv2 import ok', cv2.__version__)"

# Production is an immutable runtime. Installer-only vendored dependencies must
# not remain reachable; dependency changes go through the locked image build.
RUN python -m pip uninstall -y setuptools pip \
    && rm -rf /usr/local/lib/python3.11/ensurepip

# Read-only scripts/inspect_ai_task_worktree.py emits compact JSON (optional --pretty formatted JSON).
# Requires Git and a checkout; this image does not install Git. See docs/AI_WORKTREE_INSPECTION_GUIDE.md.
COPY . .
