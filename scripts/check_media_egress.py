"""Offline SSRF checks. No requests to real private services or metadata APIs."""
from contextlib import contextmanager
import http.client
import io
import os
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch, Mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.services import media_egress as egress


def answer(address, port=80):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, port))


@contextmanager
def allow_fixture(host, port):
    """Only the named, freshly created loopback test service is a fixture.

    Used by playback/TLS tests; private-destination rejection is tested below
    without this hook. No production setting can enable the hook.
    """
    original = egress.public_addresses
    def resolve(target, target_port):
        if (target, target_port) == (host, port):
            return [answer('127.0.0.1', port)]
        return original(target, target_port)
    with patch.object(egress, 'public_addresses', side_effect=resolve):
        yield


class EgressChecks(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {key: '' for key in ('ADMIN_BASE_URL', 'MINECRAFT_CONTROL_API_BASE', 'MEDIA_EGRESS_BLOCK_HOSTS')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_private_reserved_multicast_and_transition_addresses_rejected(self):
        for address in ('127.0.0.1', '10.1.2.3', '172.16.0.1', '192.168.1.1',
                        '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1',
                        '::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1',
                        '64:ff9b::7f00:1', '2002:7f00:1::', '2001::1'):
            with self.subTest(address=address), patch.object(egress.socket, 'getaddrinfo', return_value=[answer(address)]):
                with self.assertRaises(ValueError):
                    egress.public_addresses('attacker.invalid', 80)

    def test_mixed_dns_answers_fail_closed(self):
        with patch.object(egress.socket, 'getaddrinfo', return_value=[answer('93.184.216.34'), answer('127.0.0.1')]):
            with self.assertRaises(ValueError):
                egress.public_addresses('mixed.invalid', 80)

    def test_dns_rebinding_cannot_change_connected_address(self):
        sock = Mock()
        with patch.object(egress.socket, 'getaddrinfo', side_effect=[[answer('93.184.216.34')], [answer('127.0.0.1')]]) as dns:
            checked = egress.public_addresses('changing.invalid', 80)
            with patch.object(egress.socket, 'socket', return_value=sock):
                self.assertIs(egress.connect_checked(checked), sock)
            self.assertEqual(dns.call_count, 1)
            sock.connect.assert_called_once_with(('93.184.216.34', 80))

    def test_configured_public_management_host_is_denied(self):
        with patch.dict(os.environ, MEDIA_EGRESS_BLOCK_HOSTS='https://management.example:8000'):
            with self.assertRaises(ValueError):
                egress.public_addresses('management.example', 8000)

    def test_private_http_and_connect_never_open_destination_socket(self):
        server = egress.MediaProxy('')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(egress, 'destination_socket') as dial:
                for method, path in (('GET', 'http://127.0.0.1:1/'), ('CONNECT', '169.254.169.254:443')):
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    client.request(method, path)
                    self.assertEqual(client.getresponse().status, 403)
                    client.close()
                dial.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_public_management_address_cannot_be_reached_through_dns_alias(self):
        with patch.dict(os.environ, MINECRAFT_CONTROL_API_BASE='http://management.example:8000'), patch.object(egress.socket, 'getaddrinfo', return_value=[answer('93.184.216.34')]):
            with self.assertRaises(ValueError):
                egress.public_addresses('alias.example', 80)

    def test_http_response_redirect_is_checked_on_followup(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data/')
                self.send_header('Content-Length', '0')
                self.end_headers()
        target = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=target.serve_forever, daemon=True); thread.start()
        proxy = egress.MediaProxy('')
        worker = threading.Thread(target=proxy.serve_forever, daemon=True); worker.start()
        try:
            with allow_fixture('fixture.example', target.server_port):
                client = http.client.HTTPConnection('127.0.0.1', proxy.server_port, timeout=3)
                client.request('GET', f'http://fixture.example:{target.server_port}/')
                response = client.getresponse()
                self.assertEqual(response.status, 302)
                location = response.getheader('Location'); response.read(); client.close()
                with patch.object(egress, 'destination_socket') as dial:
                    client = http.client.HTTPConnection('127.0.0.1', proxy.server_port, timeout=3)
                    client.request('GET', location)
                    self.assertEqual(client.getresponse().status, 403); client.close()
                    dial.assert_not_called()
        finally:
            proxy.shutdown(); proxy.server_close(); worker.join()
            target.shutdown(); target.server_close(); thread.join()

    def test_vpn_receives_checked_ip_instead_of_untrusted_hostname(self):
        sock = Mock()
        sock.recv.side_effect = [bytes([c]) for c in b'HTTP/1.1 200 Connection Established\r\n\r\n']
        with patch.object(egress.socket, 'create_connection', return_value=sock):
            self.assertIs(egress.destination_socket([answer('93.184.216.34', 443)], 'http://vpn.invalid:8888'), sock)
        self.assertIn(b'CONNECT 93.184.216.34:443 HTTP/1.1\r\n', sock.sendall.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
