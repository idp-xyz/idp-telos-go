#!/usr/bin/env python3
"""PATH K (idp-host D38, idp-workspace D25): the session-bound delegation against the platform's Keycloak.

The chain a run uses, replayed from outside: bob signs in; the Workspace exchanges his token for idp-host asking for a
refresh token in his session (and keeps only the access token); idp-host exchanges that for the gateway with a refresh
token of its own; the refresh renews the gateway credential for the same user, audience and session; after bob signs
out, the refresh is refused. Exit 0 and one line of evidence, or exit 1 with the step that failed.

    python3 -I deploy/compose/session_check.py ISSUER CA_FILE CLIENTS_JSON GATEWAY_AUDIENCE
"""
import base64, json, ssl, sys, time, urllib.error, urllib.parse, urllib.request

issuer, ca, clients_file, gateway = sys.argv[1:5]
c = json.load(open(clients_file))
op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
X, AT, RT = 'urn:ietf:params:oauth:grant-type:token-exchange', 'urn:ietf:params:oauth:token-type:access_token', 'urn:ietf:params:oauth:token-type:refresh_token'


def post(path, form):
    try:
        with op.open(urllib.request.Request(issuer + path, urllib.parse.urlencode(form).encode()), timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def claims(t):
    p = t.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))


def must(step, ok, detail):
    if not ok:
        print(f'FAIL: PATH K {step}: {detail}', file=sys.stderr)
        sys.exit(1)


token = '/protocol/openid-connect/token'
st, bob = post(token, {'grant_type': 'password', 'client_id': 'e2e-cli', 'client_secret': c['e2e-cli'], 'username': 'bob', 'password': c['users']['bob']})
must('sign-in', st == 200, bob)
st, ws = post(token, {'grant_type': X, 'client_id': 'idp-workspace', 'client_secret': c['idp-workspace'], 'subject_token': bob['access_token'],
                      'subject_token_type': AT, 'audience': 'idp-host', 'requested_token_type': RT})
must('workspace exchange (session-bound)', st == 200 and 'refresh_token' in ws, ws.get('error_description', ws))
st, actor = post(token, {'grant_type': 'client_credentials', 'client_id': 'idp-host', 'client_secret': c['idp-host']})
must('idp-host actor token', st == 200, actor)
st, gw = post(token, {'grant_type': X, 'client_id': 'idp-host', 'client_secret': c['idp-host'], 'subject_token': ws['access_token'], 'subject_token_type': AT,
                      'audience': gateway, 'actor_token': actor['access_token'], 'actor_token_type': AT, 'requested_token_type': RT})
must('idp-host exchange (session-bound)', st == 200 and 'refresh_token' in gw, gw.get('error_description', gw))
first = claims(gw['access_token'])
st, renewed = post(token, {'grant_type': 'refresh_token', 'client_id': 'idp-host', 'client_secret': c['idp-host'], 'refresh_token': gw['refresh_token']})
must('renewal', st == 200, renewed)
again = claims(renewed['access_token'])
must('renewed credential', again['sub'] == first['sub'] == claims(bob['access_token'])['sub'] and again.get('sid') == first.get('sid')
     and gateway in (again['aud'] if isinstance(again['aud'], list) else [again['aud']]) and again['exp'] - int(time.time()) > 60, again)
st, _ = post('/protocol/openid-connect/logout', {'client_id': 'e2e-cli', 'client_secret': c['e2e-cli'], 'refresh_token': bob['refresh_token']})
must('sign-out', st in (200, 204), st)
st, after = post(token, {'grant_type': 'refresh_token', 'client_id': 'idp-host', 'client_secret': c['idp-host'], 'refresh_token': renewed.get('refresh_token', gw['refresh_token'])})
must('renewal after sign-out', st == 400 and after.get('error') == 'invalid_grant', (st, after))
print(f"renewed for bob ({again['sub'][:8]}…, session {str(again.get('sid'))[:8]}…, aud {gateway}, {again['exp'] - int(time.time())} s more); refused after sign-out ({after.get('error_description')})")
