#!/usr/bin/env python3
"""Offline summary regression checks; --events also verifies Java fixture output."""
import argparse
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

sys.path.insert(0, str(Path(__file__).parent / 'nethernet'))
from summarize import summarize, timestamp
from build_diagnostics import build, replace_once
from collect import collect

START = timestamp('2026-01-01T00:00:00Z')
END = timestamp('2030-01-01T00:00:00Z')


class Checks(unittest.TestCase):
    def test_empty_is_not_success(self):
        self.assertFalse(summarize([], START, END)['capture_has_connections'])

    def test_patch_drift_rejected(self):
        for text in ('absent', 'target target'):
            with self.assertRaises(ValueError):
                replace_once(text, 'target', 'replacement')

    def test_unsupported_jar_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            jar = Path(tmp) / 'wrong.jar'
            jar.write_bytes(b'unsupported')
            with self.assertRaises(ValueError):
                build(jar, Path(tmp) / 'output.jar')
            self.assertFalse((Path(tmp) / 'output.jar').exists())

    def test_windows_and_duplicate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'events.jsonl'
            rows = [dict(schema=1, utc=t, boot='b', connection='1', event='handshake_timeout')
                    for t in ('2026-01-02T01:00:00Z', '2026-01-02T02:00:00Z')]
            file.write_text('\n'.join(map(json.dumps, rows)) + '\npartial')
            result = summarize([file, file], START, timestamp('2026-01-02T01:30:00Z'))
            self.assertEqual(result['connections'][0]['events']['handshake_timeout'], 1)
            self.assertFalse(result['connections'][0]['selected_pair_observed'])
            self.assertEqual(result['invalid_lines'], 2)

    def test_timezone_required(self):
        self.assertEqual(timestamp('2026-01-01T00:00:00.123456789Z').microsecond, 123456)
        with self.assertRaises(ValueError):
            timestamp('2026-01-01T00:00:00')
        with self.assertRaises(ValueError):
            summarize([], END, START)

    def test_collect_only_diagnostics_and_safe_metadata(self):
        calls = []
        item = {'Image': 'sha256:fixture', 'State': {'StartedAt': 'fixture', 'Status': 'running'},
                'RestartCount': 0, 'HostConfig': {'NetworkMode': 'host'},
                'Config': {'Env': ['TOKEN=DO_NOT_COPY']}}
        def fake(argv, remote):
            calls.append(argv)
            if argv[1] == 'inspect':
                return CompletedProcess(argv, 0, json.dumps([item]).encode())
            if argv[-1].endswith('events-0.jsonl'):
                return CompletedProcess(argv, 0, b'{"schema":1}\n')
            return CompletedProcess(argv, 1, b'', b'not found')
        with tempfile.TemporaryDirectory() as tmp, patch('collect.command', side_effect=fake):
            output = Path(tmp) / 'capture'
            self.assertEqual(collect(output), 1)
            self.assertNotIn('DO_NOT_COPY', (output / 'metadata.json').read_text())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual((output / 'events-0.jsonl').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                collect(output)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all('cache' not in str(call) for call in calls))

    def test_collect_missing_logs_is_failure(self):
        item = {'Image': 'fixture', 'State': {'StartedAt': '', 'Status': 'running'},
                'RestartCount': 0, 'HostConfig': {'NetworkMode': 'host'}}
        def fake(argv, remote):
            return CompletedProcess(argv, 0, json.dumps([item]).encode()) if argv[1] == 'inspect' else CompletedProcess(argv, 1, b'')
        with tempfile.TemporaryDirectory() as tmp, patch('collect.command', side_effect=fake):
            with self.assertRaises(RuntimeError):
                collect(Path(tmp) / 'capture')


def check_java(paths):
    raw = ''.join(p.read_text() for p in paths)
    for forbidden in ('SECRET', '192.0.2.1', '2001:db8', '10.0.0.1', 'secret.example',
                      'must_not_appear_after_expiry'):
        assert forbidden not in raw, forbidden
    result = summarize(paths, START, END)
    assert result['invalid_lines'] == 0
    conn, = result['connections']
    assert conn['connection'] == '18446744073709551615'
    assert conn['signals']['in:connectrequest'] == 1
    assert conn['signals']['out_attempt:connectresponse'] == 1
    assert conn['candidates']['in:host:ipv4'] == 1
    assert conn['candidates']['in:relay:ipv6'] == 1
    assert conn['pairs']['selected:srflx/relay:succeeded'] == 1
    assert conn['selected_pair_observed']
    assert 'ice_state:CONNECTED' in conn['states']
    assert conn['events']['data_channels_established'] == 1
    print('Java fixtures: signaling, candidate types, selected pair, redaction, expiry PASS')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--events', nargs='+', type=Path)
    args = parser.parse_args()
    if args.events:
        check_java(args.events)
    unittest.main(argv=[sys.argv[0]])
