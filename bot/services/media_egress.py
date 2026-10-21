"""Loopback HTTP proxy for untrusted media URLs.

Resolve every connection, reject non-public answers, then connect to the checked
numeric address. Redirects and playlist segments must pass the same gate. A
configured VPN proxy is a trusted transport hop, never a destination exemption.
"""
import base64
import ipaddress
import os
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_servers = {}
_lock = threading.Lock()
_transition = tuple(ipaddress.ip_network(n) for n in ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32'))


def public_addresses(host, port):
    if not host or not 1 <= port <= 65535:
        raise ValueError('Invalid media destination')
    blocked = [x.strip() for x in os.getenv('MEDIA_EGRESS_BLOCK_HOSTS', '').split(',') if x.strip()]
    blocked += [os.getenv(k, '') for k in ('ADMIN_BASE_URL', 'MINECRAFT_CONTROL_API_BASE')]
    denied = {(urlsplit(x if '://' in x else '//' + x).hostname or '').lower().rstrip('.') for x in blocked}
    if host.lower().rstrip('.') in denied:
        raise ValueError('Protected media destination')
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError('Unresolved media destination')
    protected = set()
    for name in denied - {''}:
        try:
            protected.update(ipaddress.ip_address(a[4][0].split('%')[0])
                             for a in socket.getaddrinfo(name, port, type=socket.SOCK_STREAM))
        except socket.gaierror:
            continue
    for family, _, _, _, address in answers:
        ip = ipaddress.ip_address(address[0].split('%')[0])
        mapped = getattr(ip, 'ipv4_mapped', None)
        if (ip in protected or mapped in protected or not ip.is_global or ip.is_multicast or (mapped and not mapped.is_global)
                or any(ip.version == network.version and ip in network for network in _transition)):
            raise ValueError('Non-public media destination')
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise ValueError('Unsupported media destination')
    return answers


def connect_checked(answers):
    # No hostname is passed to connect(), including after retries (DNS rebinding).
    for family, socktype, protocol, _, address in answers:
        sock = socket.socket(family, socktype, protocol)
        sock.settimeout(15)
        try:
            sock.connect(address)
            return sock
        except OSError:
            sock.close()
    raise OSError('Media connection failed')


def destination_socket(answers, upstream):
    if not upstream:
        return connect_checked(answers)
    proxy = urlsplit(upstream)
    if proxy.scheme != 'http' or not proxy.hostname or proxy.path not in ('', '/'):
        raise ValueError('Invalid configured media proxy')
    # Only the configured operator-owned transport hop may use private addresses.
    sock = socket.create_connection((proxy.hostname, proxy.port or 80), timeout=15)
    ip, port = answers[0][4][:2]
    authority = ('[' + ip + ']' if ':' in ip else ip) + ':' + str(port)
    auth = ''
    if proxy.username:
        from urllib.parse import unquote
        value = unquote(proxy.username) + ':' + unquote(proxy.password or '')
        auth = 'Proxy-Authorization: Basic ' + base64.b64encode(value.encode()).decode() + '\r\n'
    try:
        sock.sendall(('CONNECT ' + authority + ' HTTP/1.1\r\nHost: ' + authority + '\r\n' + auth + '\r\n').encode('ascii'))
        reply = bytearray()
        while not reply.endswith(b'\r\n\r\n'):
            chunk = sock.recv(1)
            if not chunk or len(reply) >= 16384:
                raise OSError('Invalid upstream proxy response')
            reply.extend(chunk)
        if reply.split(b' ', 2)[1] != b'200':
            raise OSError('Upstream proxy refused connection')
        return sock
    except BaseException:
        sock.close()
        raise


class MediaProxy(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, upstream):
        self.upstream = upstream
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(('127.0.0.1', 0), MediaHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # URLs and proxy credentials must never enter exception logs.
        pass


class MediaHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    timeout = 20

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.forward(True)

    def do_GET(self):
        self.forward(False)

    do_HEAD = do_GET
    do_POST = do_GET

    def forward(self, tunnel):
        remote = None
        committed = False
        self.close_connection = True
        try:
            parsed = urlsplit('//' + self.path if tunnel else self.path)
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError('Invalid media URL')
            if not tunnel and parsed.scheme != 'http':
                raise ValueError('Invalid media URL scheme')
            port = parsed.port if parsed.port is not None else (443 if tunnel else 80)
            answers = public_addresses(parsed.hostname, port)
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) > 1:
                raise ValueError('Unsupported request framing')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 8 * 1024 * 1024 or (tunnel and length):
                raise ValueError('Oversized media request')
            body = self.rfile.read(length) if length else b''
            if len(body) != length:
                raise ValueError('Incomplete media request')
            remote = destination_socket(answers, self.server.upstream)
            if tunnel:
                self.send_response(200, 'Connection Established')
                self.end_headers()
            else:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                hostname = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
                host = hostname + (':' + str(port) if port != 80 else '')
                skip = {'host', 'connection', 'proxy-connection', 'proxy-authorization', 'transfer-encoding', 'upgrade'}
                skip.update(x.strip().lower() for x in self.headers.get('Connection', '').split(','))
                fields = [(k, v) for k, v in self.headers.items() if k.lower() not in skip]
                if any('\r' in v or '\n' in v for _, v in fields):
                    raise ValueError('Invalid media headers')
                headers = ''.join(k + ': ' + v + '\r\n' for k, v in fields)
                remote.sendall((self.command + ' ' + path + ' HTTP/1.1\r\nHost: ' + host + '\r\nConnection: close\r\n' + headers + '\r\n').encode('latin-1') + body)
            committed = True
            self.wfile.flush()
            peers = (self.connection, remote)
            while True:
                ready, _, _ = select.select(peers, (), (), 120)
                if not ready:
                    return
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is self.connection else self.connection).sendall(data)
        except (ValueError, OSError, IndexError):
            if not committed:
                self.send_error(403, 'Media destination unavailable or prohibited')
        finally:
            if remote is not None:
                remote.close()


def media_proxy_url(upstream=''):
    with _lock:
        if upstream not in _servers:
            server = MediaProxy(upstream)
            threading.Thread(target=server.serve_forever, name='media-egress', daemon=True).start()
            _servers[upstream] = server
        return 'http://127.0.0.1:' + str(_servers[upstream].server_port)
