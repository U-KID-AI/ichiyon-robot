# Pass the currently deployed, digest-pinned vendor image. Keep its entrypoint,
# game files in /data, and runtime configuration; only update distribution packages.
ARG BEDROCK_BASE_IMAGE
FROM ${BEDROCK_BASE_IMAGE}
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419+deb13u1' \
    && rm -rf /var/lib/apt/lists/*
