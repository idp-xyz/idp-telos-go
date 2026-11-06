"""idp-eer's identity configuration for the kind platform (the same principals, policy and grants as deploy/compose/e2e.sh
writes into eer.env; the workload `eer-api` is added because the Kubernetes manifests name it). Prints KEY=value lines
for `kubectl create configmap eer-identity --from-env-file`.

  python3 -I eer_identity.py CLIENTS_FILE ISSUER JWKS_URL
"""
import json
import sys

ids = json.load(open(sys.argv[1]))["ids"]
iss, jwks = sys.argv[2], sys.argv[3]
wl = "urn:eer:workload"
workloads = ("eer-api", "eer-worker", "eer-projector")
mappings = [{"issuer": iss, "subject": ids["operator"], "tenant_id": "pilot", "actor_id": "operator"},
            {"issuer": iss, "subject": ids["bob"], "tenant_id": "pilot", "actor_id": "bob"}] + \
           [{"issuer": wl, "subject": s, "tenant_id": "pilot"} for s in workloads + ("eer-configure",)]
policy = {"policy_id": "platform-acceptance", "policy_version": 1,
          "bindings": [{"issuer": iss, "subject": ids["operator"], "roles": ["runtime-operator"]},
                       {"issuer": iss, "subject": ids["bob"], "roles": ["runtime-operator"]}] +
                      [{"issuer": wl, "subject": s, "roles": ["runtime-workload"]} for s in workloads] +
                      [{"issuer": wl, "subject": "eer-configure", "roles": ["runtime-configurator"]}],
          "grants": [
              {"grant_id": "operator-general", "role": "runtime-operator", "principal_kind": "human", "actor_scope": "any",
               "actions": ["event.publish", "execution.submit", "execution.read", "decision.read", "actor.read", "plan.read", "world.read", "human_task.read"]},
              {"grant_id": "operator-mapped-actor", "role": "runtime-operator", "principal_kind": "human", "actor_scope": "mapped",
               "actions": ["execution.approve", "execution.cancel"]},
              {"grant_id": "runtime-workload", "role": "runtime-workload", "principal_kind": "workload", "actor_scope": "any",
               "actions": ["event.publish", "event.replay", "execution.submit", "execution.dispatch", "world.snapshot.create", "human_task.create", "human_task.expire"]},
              {"grant_id": "runtime-configuration", "role": "runtime-configurator", "principal_kind": "workload", "actor_scope": "any", "actions": ["configuration.apply"]}]}
issuers = [{"issuer": iss, "audience": "eer-runtime", "jwks_url": jwks}]
for k, v in (("EER_PRINCIPAL_MAPPINGS", mappings), ("EER_AUTHORIZATION_POLICY", policy), ("EER_OIDC_ISSUERS", issuers)):
    print(k + "=" + json.dumps(v, separators=(",", ":")))
