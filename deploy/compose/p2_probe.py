"""Probes of PATH P2 (standard library only; read from stdin by `python -`, in a container on a network of compose or in a pod of the cluster, as the policies see it):

  python - connect HOST PORT      exit 0 when a TCP connection is made in 3 seconds, 1 when not
  python - forged URL KIND        POST (KIND write) or GET (KIND read, path /content) a ticket that is forged to the CMI's file endpoint and print the HTTP status:
                                  a three-part token of the right shape (HS256 header, claims of a write ticket for the endpoint) signed with a key the CMI does not have
  python - noticket URL           POST without a ticket and print the HTTP status
"""
import base64
import hashlib
import hmac
import json
import socket
import sys
import time
import urllib.error
import urllib.request


def b64(x):
    return base64.urlsafe_b64encode(x).rstrip(b'=')


def status(req):
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


cmd = sys.argv[1]
if cmd == 'connect':
    try:
        socket.create_connection((sys.argv[2], int(sys.argv[3])), 3).close()
    except OSError:
        sys.exit(1)
elif cmd == 'forged':
    url, kind = sys.argv[2], sys.argv[3]
    head = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    claims = b64(json.dumps({'tenant_id': '00000000-0000-4000-8000-000000000001', 'sub': 'service-account-idp-cmi-ingest', 'kind': kind, 'aud': url, 'iss': 'idp-cmi',
                             'exp': int(time.time()) + 300, 'iat': int(time.time()), 'jti': 'forged-' + str(time.time_ns())}).encode())
    sig = b64(hmac.new(b'a key that the CMI does not have, 32+ bytes', head + b'.' + claims, hashlib.sha256).digest())
    ticket = (head + b'.' + claims + b'.' + sig).decode()
    if kind == 'read':
        print(status(urllib.request.Request(url.rstrip('/') + '/content', headers={'Authorization': 'Bearer ' + ticket})))
    else:
        print(status(urllib.request.Request(url, data=b'forged result', method='POST', headers={'Authorization': 'Bearer ' + ticket})))
elif cmd == 'noticket':
    print(status(urllib.request.Request(sys.argv[2], data=b'x', method='POST')))
else:
    sys.exit(2)
