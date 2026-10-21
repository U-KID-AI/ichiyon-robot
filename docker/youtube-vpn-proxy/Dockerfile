FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

RUN apk upgrade --no-cache && apk add --no-cache \
    ca-certificates \
    curl \
    iproute2 \
    openvpn \
    tinyproxy \
    && test "$(apk version -t "$(apk info -v ca-certificates | sed 's/^ca-certificates-//')" 20261020-r0)" != '<'

COPY entrypoint.sh /entrypoint.sh
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf

EXPOSE 8888

CMD ["sh", "/entrypoint.sh"]
