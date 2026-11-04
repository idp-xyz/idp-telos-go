#!/usr/bin/env python3
"""The development Keycloak realm of the whole platform (ADR-0001 T5). TEST DOUBLE: dev secrets only.

Users: alice (ws-admin, cmi-admin), bob (ws-member, host-user; EER actor bob), operator (submits EER
intents), hostadmin, hostapprover. User ids are fixed here and written to CLIENTS_FILE, because idp-eer
maps principals by (issuer, sub).
Clients:
- workspace-shell   the Workspace shell (public, authorization code + PKCE), aud idp-workspace
- idp-workspace     the Workspace as token-exchange requester: a member's token -> aud idp-host / eer-runtime
- svc-eer           idp-eer's projector as an inbox producer (client credentials): aud idp-workspace, ws-producer
- eer-intelligence  idp-eer's Intelligence worker at the CMI (client credentials): aud idp-cmi, cmi-reader + cmi-writer
- idp-host-svc      the HOST's own identity at the CMI (client credentials): aud idp-cmi, cmi-reader
- idp-host, idp-cmi, eer-runtime audiences (bearer only)
- e2e-cli           the acceptance script (password grant), aud idp-workspace, idp-host, idp-cmi, eer-runtime

  python3 deploy/keycloak/realm.py OUT_DIR TENANT_ID CLIENTS_FILE
"""
import json
import os
import secrets
import sys
import uuid

out, tenant, clients_file = sys.argv[1:4]
SHELL = "http://127.0.0.1:8560"


def hardcoded(name, value, json_type="String"):
    return {"name": name, "protocol": "openid-connect", "protocolMapper": "oidc-hardcoded-claim-mapper",
            "config": {"claim.name": name, "claim.value": value, "jsonType.label": json_type, "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}


def audience(a):
    return {"name": "aud-" + a, "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
            "config": {"included.client.audience": a, "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}


tenant_attr = {"name": "tenant_id", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
               "config": {"user.attribute": "tenant_id", "claim.name": "tenant_id", "jsonType.label": "String", "access.token.claim": "true", "id.token.claim": "true", "introspection.token.claim": "true"}}
roles_map = {"name": "roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
             "config": {"claim.name": "roles", "jsonType.label": "String", "multivalued": "true", "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}
username = {"name": "preferred_username", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
            "config": {"user.attribute": "username", "claim.name": "preferred_username", "jsonType.label": "String", "access.token.claim": "true", "id.token.claim": "true"}}
user_mappers = [tenant_attr, roles_map, username]

users = {"alice": ["ws-admin", "cmi-admin"], "bob": ["ws-member", "host-user"], "operator": [], "hostadmin": ["host-admin"], "hostapprover": ["host-approver"]}
passwords = {u: secrets.token_urlsafe(16) for u in users}
ids = {u: str(uuid.uuid4()) for u in users}
secret = {c: secrets.token_urlsafe(32) for c in ("idp-workspace", "idp-host-svc", "svc-eer", "eer-intelligence", "e2e-cli")}
realm = {
    "realm": "idp", "enabled": True, "sslRequired": "none", "accessTokenLifespan": 600,
    "roles": {"realm": [{"name": r} for r in ("ws-admin", "ws-member", "ws-producer", "host-user", "host-admin", "host-approver", "cmi-admin", "cmi-writer", "cmi-reader")]},
    "users": [{"id": ids[u], "username": u, "enabled": True, "emailVerified": True, "email": u + "@example.com", "firstName": u, "lastName": "User",
               "attributes": {"tenant_id": [tenant]}, "realmRoles": roles,
               "credentials": [{"type": "password", "value": passwords[u], "temporary": False}]} for u, roles in users.items()],
    "clients": [
        {"clientId": "workspace-shell", "enabled": True, "protocol": "openid-connect", "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
         "redirectUris": [SHELL + "/callback"], "webOrigins": [SHELL], "fullScopeAllowed": True,
         "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": SHELL + "/"}, "protocolMappers": [audience("idp-workspace")] + user_mappers},
        {"clientId": "idp-workspace", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-workspace"], "standardFlowEnabled": False,
         "fullScopeAllowed": True, "attributes": {"standard.token.exchange.enabled": "true"}, "protocolMappers": [audience("idp-host"), audience("eer-runtime")] + user_mappers},
        {"clientId": "svc-eer", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["svc-eer"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False,
         "protocolMappers": [audience("idp-workspace"), hardcoded("tenant_id", tenant), hardcoded("roles", json.dumps(["ws-producer"]), "JSON"), username]},
        {"clientId": "idp-host-svc", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-host-svc"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False, "attributes": {"access.token.lifespan": "3600"},
         "protocolMappers": [audience("idp-cmi"), hardcoded("tenant_id", tenant), hardcoded("roles", json.dumps(["cmi-reader"]), "JSON"), username]},
        {"clientId": "eer-intelligence", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["eer-intelligence"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False,
         "protocolMappers": [audience("idp-cmi"), hardcoded("tenant_id", tenant), hardcoded("roles", json.dumps(["cmi-reader", "cmi-writer"]), "JSON"), username]},
        {"clientId": "idp-host", "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        {"clientId": "idp-cmi", "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        {"clientId": "eer-runtime", "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        {"clientId": "e2e-cli", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["e2e-cli"], "standardFlowEnabled": False,
         "directAccessGrantsEnabled": True, "fullScopeAllowed": True,
         "protocolMappers": [audience("idp-workspace"), audience("idp-host"), audience("idp-cmi"), audience("eer-runtime")] + user_mappers},
    ],
}
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "idp-realm.json"), "w") as f:
    json.dump(realm, f, indent=1)
os.chmod(os.path.join(out, "idp-realm.json"), 0o644)
with open(clients_file, "w") as f:
    json.dump({"users": passwords, "ids": ids, **secret}, f, indent=1)
os.chmod(clients_file, 0o600)
print("OK: realm idp with", len(realm["clients"]), "clients and", len(users), "users")
