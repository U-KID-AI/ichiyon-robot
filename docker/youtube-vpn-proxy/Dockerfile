FROM alpine:3.20

RUN apk add --no-cache \
    ca-certificates \
    curl \
    iproute2 \
    openvpn \
    tinyproxy

COPY entrypoint.sh /entrypoint.sh
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf

EXPOSE 8888

CMD ["sh", "/entrypoint.sh"]
