#!/usr/bin/env python3
"""PATH L, the egress proxy's own record (its log on stdin, JSON lines): the tenant the gateway verified was let
through to its listed host and to nothing else, was refused the host that is not listed, and no credential was logged.
Exit 0 and one line of evidence, or exit 1 with what was wrong.

    <the proxy's log> | python3 -I deploy/compose/egress_log.py TENANT LISTED_HOST UNLISTED_HOST
"""
import json, sys

tenant, origin, unlisted = sys.argv[1:4]
raw = sys.stdin.read()
log = [json.loads(l) for l in raw.splitlines() if l.startswith('{')]
allows = [l for l in log if l.get('decision') == 'allow']
denies = [l for l in log if l.get('decision') == 'deny']
problems = []
if not any(l.get('tenant') == tenant and l.get('host') == origin and l.get('status') == 200 for l in allows):
    problems.append(f'no request of the tenant to {origin} was let through: {allows}')
if not all(l.get('tenant') == tenant and l.get('host') == origin for l in allows):
    problems.append(f'the proxy let something else through: {allows}')
if not any(l.get('tenant') == tenant and l.get('host') == unlisted and l.get('why') == 'not on the allow-list' for l in denies):
    problems.append(f'no request of the tenant to {unlisted} was refused: {denies}')
if 'Proxy-Authorization' in raw or 'eyJ' in raw:
    problems.append('a credential in the log')
if problems:
    for p in problems:
        print('FAIL: PATH L the egress proxy\'s log: ' + p, file=sys.stderr)
    sys.exit(1)
print(f'proxy records: {len(allows)} allowed (tenant {tenant[:8]}…, {origin}), {len(denies)} refused')
