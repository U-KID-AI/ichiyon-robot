#!/usr/bin/env python3
"""Summarize structured NetherNet diagnostics by boot/connection and UTC trial window.

Input is the bounded events-*.jsonl files, not raw MCXboxBroadcast debug logs.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import re
from pathlib import Path


def timestamp(value):
    # Java Instant emits nanoseconds; Python 3.8 accepts only milliseconds/microseconds.
    value = re.sub(r'\.(\d+)', lambda m: '.' + (m[1] + '000000')[:6], value, count=1)
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamps must include a timezone')
    return dt


def summarize(paths, start, end):
    if start >= end:
        raise ValueError('start must precede end')
    connections = {}
    global_events = Counter()
    server_counts = []
    skipped = 0
    seen = set()
    for path in paths:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row['schema'] != 1 or not start <= timestamp(row['utc']) <= end:
                        continue
                    # Rotated/copied files may overlap; retain one instance of each record.
                    identity = json.dumps(row, sort_keys=True)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    key = (row['boot'], row['connection'])
                    if key[1] == '0':
                        global_events[row['event']] += 1
                        if row['event'] == 'ice_servers':
                            server_counts.append({'utc': row['utc'], 'servers': row['servers'], 'urls': row['urls']})
                        continue
                    conn = connections.setdefault(key, {'first_utc': row['utc'], 'last_utc': row['utc'],
                        'events': Counter(), 'signals': Counter(), 'candidates': Counter(),
                        'states': set(), 'errors': Counter(), 'stats': {}, 'pairs': []})
                    conn['first_utc'] = min(conn['first_utc'], row['utc'])
                    conn['last_utc'] = max(conn['last_utc'], row['utc'])
                    conn['events'][row['event']] += 1
                    if row['event'] == 'signal':
                        conn['signals'][row['direction'] + ':' + row['type']] += 1
                    elif row['event'] == 'candidate':
                        conn['candidates'][row['direction'] + ':' + row['type'] + ':' + row['address_family']] += 1
                    elif row['event'] in ('ice_state', 'peer_state', 'gathering_state'):
                        conn['states'].add(row['event'] + ':' + row['state'])
                    elif row['event'] == 'ice_candidate_error':
                        conn['errors'][str(row['code'])] += 1
                    elif row['event'] == 'ice_stats':
                        conn['stats'][(row['snapshot'], row['id'])] = row
                        if row['stats_type'] == 'CANDIDATE_PAIR':
                            conn['pairs'].append(row)
                except (ValueError, KeyError, TypeError):
                    skipped += 1
    results = []
    for (boot, connection), conn in sorted(connections.items()):
        pairs = Counter()
        evidence = {}
        for pair in conn.pop('pairs'):
            candidates = []
            for side in ('local', 'remote'):
                candidate = conn['stats'].get((pair['snapshot'], pair[side]), {})
                candidates.append(candidate.get('candidate_type', 'unknown'))
            detail = ('selected' if pair['selected'] else 'unselected') + ':' + '/'.join(candidates) + ':' + pair['state']
            pairs[detail] += 1
            record = {key: pair[key] for key in ('utc', 'id', 'state', 'selected', 'nominated',
                'requestsSent', 'requestsReceived', 'responsesSent', 'responsesReceived',
                'bytesSent', 'bytesReceived', 'currentRoundTripTime') if key in pair}
            record.update(local_type=candidates[0], remote_type=candidates[1])
            if pair['id'] not in evidence or pair['utc'] > evidence[pair['id']]['utc']:
                evidence[pair['id']] = record
        del conn['stats']
        conn['states'] = sorted(conn['states'])
        conn['pairs'] = pairs
        conn['latest_pair_evidence'] = list(evidence.values())
        conn['selected_pair_observed'] = any(k.startswith('selected:') for k in pairs)
        # Absence is missing evidence, not proof that no TURN server/candidate existed.
        conn['interpretation'] = 'compare evidence; missing events do not prove network cause'
        results.append({'boot': boot, 'connection': connection, **conn})
    return {'connections': results, 'global_events': global_events, 'ice_server_counts': server_counts, 'invalid_lines': skipped,
            'capture_has_connections': bool(results)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', type=Path, nargs='+')
    parser.add_argument('--start', required=True, type=timestamp)
    parser.add_argument('--end', required=True, type=timestamp)
    args = parser.parse_args()
    print(json.dumps(summarize(args.files, args.start, args.end), indent=2, ensure_ascii=False))
