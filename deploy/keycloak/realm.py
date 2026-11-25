#!/usr/bin/env python3
"""The development Keycloak realm of the whole platform (ADR-0001 T5). TEST DOUBLE: dev secrets only.

Users: alice (ws-admin, cmi-admin, host-user), bob (ws-member, host-user; EER actor bob), carol (ws-member, host-user, cmi-reader: bob's colleague, PATH P0), operator (submits EER
intents), hostadmin, hostapprover, bridgeop (ws-member and bridge-reviewer: the operator who authorizes the reviewer account of a
bridge connection, idp-mcp-bridge RUNBOOK 5.6). User ids are fixed here and written to CLIENTS_FILE, because idp-eer
maps principals by (issuer, sub).
Clients:
- workspace-shell   the Workspace shell (public, authorization code + PKCE), aud idp-workspace
- idp-workspace     the Workspace as token-exchange requester: a member's token -> aud idp-host / eer-runtime / idp-cmi (a member's own memory) /
                    idp-mcp-bridge (the bridge's internal API: connected services, idp-workspace D30); the exchanged token keeps `roles` and
                    `tenant_id` (the bridge reads the member, the tenant and the reviewer role from them)
- svc-eer           idp-eer's projector as an inbox producer (client credentials): aud idp-workspace, ws-producer
- eer-intelligence  idp-eer's Intelligence worker at the CMI (client credentials): aud idp-cmi, cmi-reader + cmi-writer
- idp-host-svc      the HOST's own identity at the CMI (client credentials): aud idp-cmi, cmi-reader + cmi-writer (it writes conversation memory)
- idp-host          the HOST as RFC 8693 token-exchange requester: a member's token (aud idp-host) -> a token for the idp-mcp
                    gateway (aud GATEWAY, azp = idp-host) or for the CMI (aud idp-cmi: the CMI then sees the member, so a
                    member's own memories are the ones retrieved)
- GATEWAY, idp-cmi, eer-runtime, idp-mcp-bridge audiences (bearer only; GATEWAY = the gateway's canonical MCP URI; idp-mcp-bridge is
                    the client that stands for the bridge: its client id is IDP_BRIDGE_API_AUDIENCE)
- idp-probe         ACCEPTANCE ONLY: a platform client that is not the HOST, exchanging a member's token for the gateway (PATH M2)
- idp-bridge-directory  DEV/TEST ONLY: the dev/test directory adapter of idp-mcp-bridge (IDP_BRIDGE_DIRECTORY=keycloak-dev, offboarding plan F1): a confidential
                    client with a service account that holds only realm-management view-users (it reads a user's state: found, enabled, tenant attribute).
                    Keycloak is the dev/test identity provider; the final one is not chosen, and this adapter is replaced by its directory or SCIM query
- idp-lifecycle-svc DEV/TEST ONLY: stands for the future "member lifecycle service" (offboarding plan section 1, layer 2: SCIM or SSF in the final identity
                    system): a confidential client-credentials client whose tokens carry the bridge's audience; it is the only `azp` in the bridge's
                    IDP_BRIDGE_OFFBOARD_CLIENTS. The bridge refuses a token with a session (`sid`) or a delegation (`act`): Keycloak's client-credentials
                    tokens have neither unless the client keeps a session (see the comment at the client)
- e2e-cli           the acceptance script (password grant), aud idp-workspace, idp-host, idp-probe, idp-cmi, eer-runtime

  python3 deploy/keycloak/realm.py OUT_DIR TENANT_ID CLIENTS_FILE
"""
import json
import os
import secrets
import sys
import uuid

out, tenant, clients_file = sys.argv[1:4]
SHELL = os.environ.get("IDP_REALM_SHELL", "http://127.0.0.1:8560")   # the Workspace shell's origin as the browser opens it (kind: its port-forward)
GATEWAY = os.environ.get("IDP_REALM_GATEWAY", "http://mcp-gateway:3320/mcp")   # the idp-mcp gateway as the HOST calls it (kind: its Service URL)


def hardcoded(name, value, json_type="String"):
    return {"name": name, "protocol": "openid-connect", "protocolMapper": "oidc-hardcoded-claim-mapper",
            "config": {"claim.name": name, "claim.value": value, "jsonType.label": json_type, "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}


def audience(a):
    return {"name": "aud-" + a, "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
            "config": {"included.client.audience": a, "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}


def audience_uri(a):  # an audience that is not a client id: a URI
    return {"name": "aud-" + a[-24:], "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
            "config": {"included.custom.audience": a, "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}


tenant_attr = {"name": "tenant_id", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
               "config": {"user.attribute": "tenant_id", "claim.name": "tenant_id", "jsonType.label": "String", "access.token.claim": "true", "id.token.claim": "true", "introspection.token.claim": "true"}}
roles_map = {"name": "roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
             "config": {"claim.name": "roles", "jsonType.label": "String", "multivalued": "true", "access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "true"}}
username = {"name": "preferred_username", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
            "config": {"user.attribute": "username", "claim.name": "preferred_username", "jsonType.label": "String", "access.token.claim": "true", "id.token.claim": "true"}}
user_mappers = [tenant_attr, roles_map, username]

users = {"alice": ["ws-admin", "cmi-admin", "host-user"], "bob": ["ws-member", "host-user", "cmi-reader"], "carol": ["ws-member", "host-user", "cmi-reader"], "operator": [], "hostadmin": ["host-admin"], "hostapprover": ["host-approver"],
         "bridgeop": ["ws-member", "bridge-reviewer"]}
passwords = {u: secrets.token_urlsafe(16) for u in users}
ids = {u: str(uuid.uuid4()) for u in users}
secret = {c: secrets.token_urlsafe(32) for c in ("idp-workspace", "idp-host-svc", "idp-host", "idp-probe", "svc-eer", "eer-intelligence", "e2e-cli", "idp-bridge-directory", "idp-lifecycle-svc")}
realm = {
    "realm": "idp", "enabled": True, "sslRequired": "none", "accessTokenLifespan": 600,
    "roles": {"realm": [{"name": r} for r in ("ws-admin", "ws-member", "ws-producer", "host-user", "host-admin", "host-approver", "cmi-admin", "cmi-writer", "cmi-reader", "bridge-reviewer")]},
    "users": [{"username": "service-account-idp-bridge-directory", "enabled": True, "serviceAccountClientId": "idp-bridge-directory",
               "clientRoles": {"realm-management": ["view-users"]}}] + [{"id": ids[u], "username": u, "enabled": True, "emailVerified": True, "email": u + "@example.com", "firstName": u, "lastName": "User",
               "attributes": {"tenant_id": [tenant]}, "realmRoles": roles,
               "credentials": [{"type": "password", "value": passwords[u], "temporary": False}]} for u, roles in users.items()],
    "clients": [
        {"clientId": "workspace-shell", "enabled": True, "protocol": "openid-connect", "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
         "redirectUris": [SHELL + "/callback"], "webOrigins": [SHELL], "fullScopeAllowed": True,
         "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": SHELL + "/"}, "protocolMappers": [audience("idp-workspace")] + user_mappers},
        # standard.token.exchange.enableRefreshRequestedTokenType=SAME_SESSION (idp-host D38, idp-workspace D25): an exchange
        # may ask for a refresh token in the member's own session. The Workspace asks and discards it (its exchanged token
        # then has a client session that idp-host may exchange again); idp-host keeps its own and renews a run's gateway
        # credential with it until the member signs out or the session reaches its limits.
        {"clientId": "idp-workspace", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-workspace"], "standardFlowEnabled": False,
         "fullScopeAllowed": True, "attributes": {"standard.token.exchange.enabled": "true", "standard.token.exchange.enableRefreshRequestedTokenType": "SAME_SESSION"}, "protocolMappers": [audience("idp-host"), audience("eer-runtime"), audience("idp-cmi"), audience("idp-mcp-bridge")] + user_mappers},
        {"clientId": "svc-eer", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["svc-eer"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False,
         "protocolMappers": [audience("idp-workspace"), hardcoded("tenant_id", tenant), hardcoded("roles", json.dumps(["ws-producer"]), "JSON"), username]},
        {"clientId": "idp-host-svc", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-host-svc"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False, "attributes": {"access.token.lifespan": "3600"},
         "protocolMappers": [audience("idp-cmi"), hardcoded("tenant_id", tenant), hardcoded("roles", json.dumps(["cmi-reader", "cmi-writer"]), "JSON"), username]},
        {"clientId": "eer-intelligence", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["eer-intelligence"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False,
         "protocolMappers": [audience("idp-cmi"), hardcoded("tenant_id", tenant), hardcoded("roles", json.dumps(["cmi-reader", "cmi-writer"]), "JSON"), username]},
        {"clientId": "idp-host", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-host"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": True, "attributes": {"standard.token.exchange.enabled": "true", "standard.token.exchange.enableRefreshRequestedTokenType": "SAME_SESSION"},
         "protocolMappers": [audience_uri(GATEWAY), audience("idp-cmi")] + user_mappers},
        # ACCEPTANCE ONLY: a platform client that is not the HOST and can obtain a gateway-audience token for a member (the same
        # exchange idp-host does, without its actor token). The gateway lists it as a platform client that is not the host, and
        # PATH M2 asserts that the host-only web tools are neither visible nor callable through it (idp-mcp ADR-0001 D48). No
        # product component uses it.
        {"clientId": "idp-probe", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-probe"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": True, "attributes": {"standard.token.exchange.enabled": "true"},
         "protocolMappers": [audience_uri(GATEWAY)] + user_mappers},
        {"clientId": GATEWAY, "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        {"clientId": "idp-cmi", "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        {"clientId": "eer-runtime", "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        {"clientId": "idp-mcp-bridge", "enabled": True, "protocol": "openid-connect", "bearerOnly": True, "publicClient": False, "standardFlowEnabled": False},
        # DEV/TEST ONLY (see the docstring): the directory adapter's own client; its service account has view-users and nothing else, no audience, no mappers
        {"clientId": "idp-bridge-directory", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-bridge-directory"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": True,   # fullScopeAllowed: the token must carry the account's one role (realm-management view-users); the account has no other
         # since Keycloak 26.8 the admin REST API accepts only a token that carries the client scope kc-admin-api (its audience and its scope): without it the answer is 401
         # "Token is missing the required admin API scope", whatever roles the service account has
         "defaultClientScopes": ["kc-admin-api"]},
        # DEV/TEST ONLY: the stand-in for the lifecycle service. A client-credentials token of this client carries the bridge's audience and no `sid` (a
        # `sid` appears only when the client keeps a session for it: the client attribute client_credentials.use_refresh_token / a session for service
        # accounts, which is off here and must stay off: the bridge refuses a token with `sid`)
        {"clientId": "idp-lifecycle-svc", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["idp-lifecycle-svc"], "standardFlowEnabled": False,
         "serviceAccountsEnabled": True, "fullScopeAllowed": False, "protocolMappers": [audience("idp-mcp-bridge")]},
        {"clientId": "e2e-cli", "enabled": True, "protocol": "openid-connect", "publicClient": False, "secret": secret["e2e-cli"], "standardFlowEnabled": False,
         "directAccessGrantsEnabled": True, "fullScopeAllowed": True,
         "protocolMappers": [audience("idp-workspace"), audience("idp-host"), audience("idp-probe"), audience("idp-cmi"), audience("eer-runtime")] + user_mappers},
    ],
}
# DEV/TEST ONLY (the dev/test directory adapter of idp-mcp-bridge): Keycloak's admin API shows a user's attributes (tenant_id) only when the realm's user profile
# allows unmanaged attributes for admins: ADMIN_VIEW (readable by an administrator and the view-users service account, not editable by the user). The four default attributes
# are Keycloak's own profile, unchanged. Without it the adapter cannot compare the tenant and tells "unknown" for every enabled user
USER_PROFILE = {"attributes": [
    {"name": "username", "displayName": "${username}", "validations": {"length": {"min": 3, "max": 255}, "username-prohibited-characters": {}, "up-username-not-idn-homograph": {}},
     "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}, "multivalued": False},
    {"name": "email", "displayName": "${email}", "validations": {"email": {}, "length": {"max": 255}}, "required": {"roles": ["user"]},
     "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}, "multivalued": False},
    {"name": "firstName", "displayName": "${firstName}", "validations": {"length": {"max": 255}, "person-name-prohibited-characters": {}}, "required": {"roles": ["user"]},
     "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}, "multivalued": False},
    {"name": "lastName", "displayName": "${lastName}", "validations": {"length": {"max": 255}, "person-name-prohibited-characters": {}}, "required": {"roles": ["user"]},
     "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}, "multivalued": False}],
    "groups": [{"name": "user-metadata", "displayHeader": "User metadata", "displayDescription": "Attributes, which refer to user metadata"}],
    "unmanagedAttributePolicy": "ADMIN_VIEW"}
realm["components"] = {"org.keycloak.userprofile.UserProfileProvider": [{"providerId": "declarative-user-profile", "subComponents": {}, "config": {"kc.user.profile.config": [json.dumps(USER_PROFILE)]}}]}
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "idp-realm.json"), "w") as f:
    json.dump(realm, f, indent=1)
os.chmod(os.path.join(out, "idp-realm.json"), 0o644)
with open(clients_file, "w") as f:
    json.dump({"users": passwords, "ids": ids, **secret}, f, indent=1)
os.chmod(clients_file, 0o600)
print("OK: realm idp with", len(realm["clients"]), "clients and", len(users), "users")
