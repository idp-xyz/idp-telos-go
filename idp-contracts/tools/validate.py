"""File-level validation of the idp.telos.* contracts. Does not run any producer.

For every namespace in registry.json that has a schema: the schema is valid JSON Schema 2020-12,
every $ref resolves, every definition carries title/description/x-idp-telos-owner, the producer-
generated examples validate, the hand-written positive and negative cases behave as expected, the
registry entries match the definitions, and the published files are byte-identical to the
producer's copies. A missing producer checkout is reported as NOT_RUN (exit 2), never as a reduced
check.

Usage: python idp-contracts/tools/validate.py [--root CONTRACTS_DIR] [--producer-root DIR] [--report PATH]
  --producer-root holds the producer repositories (default: the parent of --root, where idp-mcp/
  is a sibling of idp-contracts/).
Exit codes: 0 all passed; 1 a check failed; 2 a check could not run (producer checkout missing).
"""
import argparse
import copy
import datetime
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import best_match
    from referencing import Registry, Resource
except ImportError:
    raise SystemExit('Install pinned dependencies from idp-contracts/requirements.txt; no reduced validator fallback is used.')

HERE = Path(__file__).resolve()
OWNER_KEY = 'x-idp-telos-owner'

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=HERE.parents[1])
parser.add_argument('--producer-root', type=Path)
parser.add_argument('--report', type=Path)
args = parser.parse_args()
ROOT = args.root.resolve()
PRODUCER_ROOT = (args.producer_root or ROOT.parent).resolve()
REPORT = args.report or ROOT / 'checks/validation_report.json'


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding='utf-8'))


reg = load('registry.json')
NAMESPACES = {ns: n for ns, n in reg['namespaces'].items() if n.get('schema')}
schemas = {ns: load(n['schema']) for ns, n in NAMESPACES.items()}
# Examples are produced by each namespace's producer: {"definition": <$defs name>, "value": <document>}.
examples = {ns: [(x['definition'], x['value']) for x in load(n['examples'])['examples']] for ns, n in NAMESPACES.items()}
for s in schemas.values():
    Draft202012Validator.check_schema(s)
registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas.values())
validators = {ns: Draft202012Validator(s, registry=registry, format_checker=FormatChecker()) for ns, s in schemas.items()}
roots = {ns: {r['$ref'].split('/')[-1] for r in s.get('oneOf', [])} for ns, s in schemas.items()}

results = []
invariants = {}


def by_def(ns, definition):
    """A validator for one definition, so a rejection names the rule that failed rather than the root oneOf."""
    return Draft202012Validator({'$ref': f'{schemas[ns]["$id"]}#/$defs/{definition}'}, registry=registry,
                                format_checker=FormatChecker())


def check(name, ns, definition, value, expect_valid):
    errors = list(by_def(ns, definition).iter_errors(value))
    passed = (not errors) == expect_valid
    if definition in roots[ns]:
        # The root oneOf must dispatch to the same answer for the namespace's top-level documents.
        root_errors = list(validators[ns].iter_errors(value))
        passed = passed and (not root_errors) == (not errors)
        errors = errors or root_errors
    reason = best_match(errors)
    results.append({'case': name, 'namespace': ns, 'definition': definition, 'expected_valid': expect_valid, 'passed': passed,
                    'errors': [f'{"/".join(map(str, reason.absolute_path)) or "<root>"}: {reason.message[:200]}']
                    if reason else []})
    if not passed:
        print(json.dumps(results[-1], ensure_ascii=False), file=sys.stderr)


def invariant(name, ok, detail=None):
    invariants[name] = {'passed': bool(ok), **({'detail': detail} if detail and not ok else {})}
    if not ok:
        print(f'INVARIANT FAILED {name}: {detail}', file=sys.stderr)


def pick(ns, definition, index=0):
    return copy.deepcopy([v for d, v in examples[ns] if d == definition][index])


def S(**kw):
    return lambda x: x.update(kw)


def U(*keys):
    return lambda x: [x.pop(k, None) for k in keys]


def P(path, value):
    """Set a nested value by dotted path, list indexes as numbers: P('targets.0.name', 'x')."""
    def mutate(x):
        node = x
        keys = path.split('.')
        for k in keys[:-1]:
            node = node[int(k)] if isinstance(node, list) else node[k]
        k = keys[-1]
        if isinstance(node, list):
            node[int(k)] = value
        else:
            node[k] = value
    return mutate


# 1. Producer-generated examples: all valid
for ns, items in examples.items():
    for i, (definition, x) in enumerate(items):
        check(f'example:{definition}#{i}', ns, definition, x, True)

# 2. Hand-written cases: (name, namespace, definition, example index, mutations...).
#    idp.telos.mcp: the negatives mirror idp-mcp's TestContractRejectsWhatValidateRejects.
MCP = 'idp.telos.mcp'
MCP_JWT = {'issuer': 'https://idp.example/realms/idp', 'resource': 'https://mcp.example/mcp', 'jwksUrl': 'https://idp.example/realms/idp/certs',
           'authorizationServers': ['https://idp.example/realms/idp'], 'tenantClaim': 'tenant_id', 'rolesClaim': 'roles'}

POSITIVE = [
    ('release_without_approver', MCP, 'Release', 0, U('approvedBy')),
    ('release_report_drift', MCP, 'Release', 0, S(driftAction='report')),
    ('release_grant_prompts_only', MCP, 'Release', 0, P('grants.1', {'target': 'parcel', 'tools': [], 'prompts': ['shipping-label'], 'roles': ['writer']})),
    ('release_grant_resources_only', MCP, 'Release', 0, P('grants.1', {'target': 'parcel', 'tools': [], 'resources': True, 'roles': ['reader']})),
    ('release_jwt_auth', MCP, 'Release', 0, S(auth={'mode': 'jwt', 'jwt': MCP_JWT})),
    ('release_tool_with_extra_fields', MCP, 'Release', 0, P('targets.0.tools.0.execution', {'taskSupport': 'optional'}), P('targets.0.tools.0._meta', {'v': 1})),
    ('record_minimal', MCP, 'InvocationRecord', 0, U('client', 'actor', 'target', 'traceId', 'argsDigest')),
    ('record_invalid_arguments', MCP, 'InvocationRecord', 0, S(outcome='INVALID_ARGUMENTS', decision='DENY')),
]

NEGATIVE = [
    ('wrong_schema_version', MCP, 'Release', 0, S(schemaVersion='idp.mcp.release/v2')),
    ('unknown_drift_action', MCP, 'Release', 0, S(driftAction='ignore')),
    ('unknown_tool_naming', MCP, 'Release', 0, S(toolNaming='flat')),
    ('empty_release_id', MCP, 'Release', 0, S(releaseId='')),
    ('unknown_top_level_field', MCP, 'Release', 0, S(extra=True)),
    ('target_name_uppercase', MCP, 'Release', 0, P('targets.0.name', 'Parcel')),
    ('target_name_underscore', MCP, 'Release', 0, P('targets.0.name', 'par_cel')),
    ('endpoint_not_http', MCP, 'Release', 0, P('targets.0.endpoint', 'ftp://parcel/mcp')),
    ('endpoint_with_query', MCP, 'Release', 0, P('targets.0.endpoint', 'https://parcel/mcp?x=1')),
    ('unknown_network', MCP, 'Release', 0, P('targets.0.network', 'vpn')),
    ('malformed_snapshot_digest', MCP, 'Release', 0, P('targets.0.snapshotDigest', 'sha256:zz')),
    ('target_without_tools', MCP, 'Release', 0, P('targets.0.tools', [])),
    ('tool_without_input_schema', MCP, 'Release', 0, P('targets.0.tools.0', {'name': 'x'})),
    ('tool_name_too_long', MCP, 'Release', 0, P('targets.0.tools.0.name', 'a' * 129)),
    ('unknown_capability', MCP, 'Release', 0, P('targets.0.capabilities', ['tools', 'magic'])),
    ('unknown_target_field', MCP, 'Release', 0, P('targets.0.extra', True)),
    ('grant_without_roles', MCP, 'Release', 0, P('grants.0.roles', [])),
    ('grant_with_nothing', MCP, 'Release', 0, P('grants.1', {'target': 'parcel', 'tools': [], 'roles': ['viewer']})),
    ('grant_bad_role', MCP, 'Release', 0, P('grants.0.roles', ['operator', 'bad role'])),
    ('api_key_plus_jwt', MCP, 'Release', 0, P('auth.jwt', MCP_JWT)),
    ('api_key_without_keys', MCP, 'Release', 0, P('auth.apiKeys', [])),
    ('short_sha256', MCP, 'Release', 0, P('auth.apiKeys.0.sha256', 'abc')),
    ('unknown_auth_mode', MCP, 'Release', 0, P('auth.mode', 'none')),
    ('jwt_without_profile', MCP, 'Release', 0, S(auth={'mode': 'jwt'})),
    ('envelope_bad_digest', MCP, 'Envelope', 0, S(digest='sha256:short')),
    ('envelope_without_document', MCP, 'Envelope', 0, U('document')),
    ('record_unknown_decision', MCP, 'InvocationRecord', 0, S(decision='MAYBE')),
    ('record_unknown_outcome', MCP, 'InvocationRecord', 0, S(outcome='FINE')),
    ('record_negative_latency', MCP, 'InvocationRecord', 0, S(latencyMs=-1)),
    ('record_fractional_latency', MCP, 'InvocationRecord', 0, S(latencyMs=1.5)),
    ('record_bad_trace_id', MCP, 'InvocationRecord', 0, S(traceId='xyz')),
    ('record_unknown_auth_method', MCP, 'InvocationRecord', 0, S(authMethod='basic')),
    ('record_unlisted_method', MCP, 'InvocationRecord', 0, S(method='tools/list')),
    ('record_empty_subject', MCP, 'InvocationRecord', 0, S(subject='')),
    ('record_bad_release_digest', MCP, 'InvocationRecord', 0, S(releaseDigest='sha1:abc')),
    ('record_with_arguments', MCP, 'InvocationRecord', 0, S(arguments={'recipient': 'x'})),
]

#    idp.telos.host: mirrors idp-host's TestContractRejectsWhatOpenRejects
HOST = 'idp.telos.host'
if HOST in schemas:
    POSITIVE += [
        ('release_wildcard_tools', HOST, 'AgentRelease', 0, P('definition.tools.allow', ['*'])),
        ('release_without_fallbacks', HOST, 'AgentRelease', 0, P('definition.model', {'provider': 'local', 'model': 'qwen3.8-flash'})),
        ('run_with_execution_ref', HOST, 'AgentRun', 0, S(executionRef='eer:execution:1')),
        ('options_server_tools', HOST, 'RunOptions', 0, S(**{'idp.host/toolExecution': 'server'})),
        ('options_empty', HOST, 'RunOptions', 0,
         U('idp.host/toolExecution', 'idp.host/releaseDigest', 'idp.host/executionRef', 'idp.host/maxOutputTokens')),
        ('options_max_tokens_at_cap', HOST, 'RunOptions', 0, S(**{'idp.host/maxOutputTokens': 1048576})),
        # 1.3.0 (D22-D24)
        ('release_merge_prepend_union_agent', HOST, 'AgentRelease', 0, P('definition.skillMerge', {'instructions': 'prepend', 'tools': 'union', 'confirmation': 'agent'})),
        ('release_without_subagents', HOST, 'AgentRelease', 0, U('subagents'), P('definition.subagents', [])),
        ('release_depth_at_max', HOST, 'AgentRelease', 0, P('definition.limits.maxSubagentDepth', 5)),
        ('release_resource_inline_only', HOST, 'AgentRelease', 0, P('skills.0.definition.resources.1.onDemand', False)),
        ('run_awaiting_subagent', HOST, 'AgentRun', 0, S(status='AWAITING_SUBAGENT')),
        ('run_child', HOST, 'AgentRun', 0, S(parentRunId='run_01JD7Q3Z8M8T0G3C6N4V9S2A1E', depth=1)),
        # 1.4.0 (D26-D28)
        ('release_guards_off', HOST, 'AgentRelease', 0, P('definition.guards', {'input': {'toolResults': 'off', 'context': 'off'}})),
        ('release_without_evaluation', HOST, 'AgentRelease', 0, U('definition.evaluation'), P('definition.guards.output', {})),
        ('release_evaluation_minimal', HOST, 'AgentRelease', 0, P('definition.evaluation', {'cases': [{'name': 'a', 'input': 'hi', 'expect': {'noToolCall': True}}]})),
        ('run_evaluation', HOST, 'AgentRun', 0, S(purpose='evaluation')),
        # 1.5.0 (D29, D30)
        ('release_without_routing', HOST, 'AgentRelease', 0, U('subagents'), P('definition.subagents', []), P('definition.model', {'provider': 'local', 'model': 'qwen3.8-flash'})),
        ('release_routing_latency', HOST, 'AgentRelease', 0, P('definition.model.routing.strategy', 'latency')),
        ('release_candidate_minimal', HOST, 'AgentRelease', 0, P('definition.model.routing.candidates', [{'provider': 'local', 'model': 'qwen3.8-flash'}])),
        # 1.6.0 (D32)
        ('release_without_context', HOST, 'AgentRelease', 0, U('definition.context')),
        ('release_context_minimal', HOST, 'AgentRelease', 0, P('definition.context', {'cmi': True})),
        ('release_context_fail', HOST, 'AgentRelease', 0, P('definition.context.onError', 'fail')),
        ('run_with_context_package', HOST, 'AgentRun', 1, S(contextPackageId='pkg_01JD7Q3Z8M8T0G3C6N4V9S2B01')),
        # 1.7.0 (CMI D24)
        ('release_context_on_stale_fail', HOST, 'AgentRelease', 0, P('definition.context.onStale', 'fail')),
    ]
    NEGATIVE += [
        ('host_wrong_schema_version', HOST, 'AgentRelease', 0, S(schemaVersion='idp.host.agent/v2')),
        ('host_version_zero', HOST, 'AgentRelease', 0, S(version=0)),
        ('host_bad_agent_name', HOST, 'AgentRelease', 0, S(agentName='Parcel Assistant')),
        ('host_empty_instructions', HOST, 'AgentRelease', 0, P('definition.instructions', '')),
        ('host_bad_provider', HOST, 'AgentRelease', 0, P('definition.model.provider', 'Local Provider')),
        ('host_temperature_out_of_range', HOST, 'AgentRelease', 0, P('definition.model.temperature', 3)),
        ('host_bad_tool_name', HOST, 'AgentRelease', 0, P('definition.tools.allow', ['bad name'])),
        ('host_bad_confirmation_default', HOST, 'AgentRelease', 0, P('definition.confirmation.default', 'ask')),
        ('host_bad_confirmation_override', HOST, 'AgentRelease', 0, P('definition.confirmation.tools', {'x': 'maybe'})),
        ('host_steps_out_of_range', HOST, 'AgentRelease', 0, P('definition.limits.maxSteps', 1000)),
        ('host_unknown_top_level_field', HOST, 'AgentRelease', 0, S(extra=True)),
        ('host_unknown_definition_field', HOST, 'AgentRelease', 0, P('definition.extra', True)),
        ('host_skill_digest_malformed', HOST, 'AgentRelease', 0, P('skills.0.digest', 'sha256:zz')),
        ('host_skill_bad_tool', HOST, 'AgentRelease', 0, P('skills.0.definition.tools.0', 'bad name')),
        ('host_skill_ref_bad_name', HOST, 'AgentRelease', 0, P('definition.skills.0.name', 'Customs')),
        ('host_skill_release_wrong_schema', HOST, 'SkillRelease', 0, S(schemaVersion='idp.host.skill/v2')),
        ('host_skill_release_empty_instructions', HOST, 'SkillRelease', 0, P('definition.instructions', '')),
        ('host_skill_release_bad_confirmation', HOST, 'SkillRelease', 0, P('definition.confirmation', {'x': 'maybe'})),
        ('host_envelope_bad_digest', HOST, 'Envelope', 0, S(digest='sha256:short')),
        ('host_run_unknown_status', HOST, 'AgentRun', 0, S(status='DONE')),
        ('host_run_bad_id', HOST, 'AgentRun', 0, S(runId='run 1')),
        ('host_run_bad_digest', HOST, 'AgentRun', 0, S(releaseDigest='sha256:zz')),
        ('host_run_negative_steps', HOST, 'AgentRun', 0, S(stepCount=-1)),
        ('host_run_bad_trace_id', HOST, 'AgentRun', 0, S(traceId='xyz')),
        ('host_run_unknown_field', HOST, 'AgentRun', 0, S(arguments='x')),
        ('host_run_empty_owner', HOST, 'AgentRun', 0, S(owner='')),
        # RunOptions: mirrors the option cases of the same test (the API answers these with 422)
        ('host_options_unknown_key', HOST, 'RunOptions', 0, S(**{'idp.host/unknown': 1})),
        ('host_options_tool_execution_both', HOST, 'RunOptions', 0, S(**{'idp.host/toolExecution': 'both'})),
        ('host_options_tool_execution_null', HOST, 'RunOptions', 0, S(**{'idp.host/toolExecution': None})),
        ('host_options_digest_malformed', HOST, 'RunOptions', 0, S(**{'idp.host/releaseDigest': 'sha256:zz'})),
        ('host_options_digest_uppercase', HOST, 'RunOptions', 0, S(**{'idp.host/releaseDigest': 'sha256:' + 'AB' * 32})),
        ('host_options_max_tokens_zero', HOST, 'RunOptions', 0, S(**{'idp.host/maxOutputTokens': 0})),
        ('host_options_max_tokens_over_cap', HOST, 'RunOptions', 0, S(**{'idp.host/maxOutputTokens': 1048577})),
        ('host_options_max_tokens_as_text', HOST, 'RunOptions', 0, S(**{'idp.host/maxOutputTokens': '512'})),
        ('host_options_execution_ref_empty', HOST, 'RunOptions', 0, S(**{'idp.host/executionRef': ''})),
        ('host_options_execution_ref_space', HOST, 'RunOptions', 0, S(**{'idp.host/executionRef': 'has space'})),
        ('host_options_execution_ref_too_long', HOST, 'RunOptions', 0, S(**{'idp.host/executionRef': 'x' * 257})),
        ('host_options_execution_ref_tab', HOST, 'RunOptions', 0, S(**{'idp.host/executionRef': 'tab\there'})),
        # a caller's own forwardedProps keys are outside the contract object (the API ignores them)
        ('host_options_foreign_key', HOST, 'RunOptions', 0, S(**{'app/theme': 'dark'})),
        # 1.3.0 (D22-D24)
        ('host_subagent_digest_malformed', HOST, 'AgentRelease', 0, P('subagents.0.digest', 'sha256:zz')),
        ('host_subagent_ref_no_description', HOST, 'AgentRelease', 0, P('definition.subagents.0.description', '')),
        ('host_subagent_ref_bad_name', HOST, 'AgentRelease', 0, P('definition.subagents.0.name', 'Tariff Expert')),
        ('host_subagent_embedded_no_agent_id', HOST, 'AgentRelease', 0, P('subagents.0.agentId', '')),
        ('host_merge_rule_unknown', HOST, 'AgentRelease', 0, P('definition.skillMerge.tools', 'mix')),
        ('host_merge_rule_incomplete', HOST, 'AgentRelease', 0, P('definition.skillMerge', {'instructions': 'append'})),
        ('host_depth_out_of_range', HOST, 'AgentRelease', 0, P('definition.limits.maxSubagentDepth', 9)),
        ('host_depth_zero', HOST, 'AgentRelease', 0, P('definition.limits.maxSubagentDepth', 0)),
        ('host_resource_bad_media_type', HOST, 'AgentRelease', 0, P('skills.0.definition.resources.0.mediaType', 'image/png')),
        ('host_resource_empty_content', HOST, 'AgentRelease', 0, P('skills.0.definition.resources.0.content', '')),
        ('host_resource_bad_name', HOST, 'AgentRelease', 0, P('skills.0.definition.resources.0.name', 'has space')),
        ('host_reserved_allow_entry', HOST, 'AgentRelease', 0, P('definition.tools.allow.0', 'agent__tariff-expert')),
        ('host_reserved_skill_tool', HOST, 'AgentRelease', 0, P('skills.0.definition.tools.0', 'resource__read')),
        ('host_skill_release_reserved_tool', HOST, 'SkillRelease', 0, P('definition.tools.0', 'agent__x')),
        ('host_skill_release_bad_resource', HOST, 'SkillRelease', 0, P('definition.resources.0.mediaType', 'text/html')),
        ('host_run_negative_depth', HOST, 'AgentRun', 0, S(depth=-1)),
        ('host_run_bad_parent_id', HOST, 'AgentRun', 0, S(parentRunId='run 1')),
        # 1.4.0 (D26-D28)
        ('host_guard_input_mode_unknown', HOST, 'AgentRelease', 0, P('definition.guards.input.toolResults', 'sanitize')),
        ('host_guard_input_incomplete', HOST, 'AgentRelease', 0, P('definition.guards.input', {'toolResults': 'wrap'})),
        ('host_guard_redact_unknown', HOST, 'AgentRelease', 0, P('definition.guards.output.redact.0', 'ssn')),
        ('host_guard_on_error_unknown', HOST, 'AgentRelease', 0, P('definition.guards.output.onError', 'retry')),
        ('host_guard_pattern_empty', HOST, 'AgentRelease', 0, P('definition.guards.output.patterns.0', '')),
        ('host_guard_classifier_bad_name', HOST, 'AgentRelease', 0, P('definition.guards.output.classifier', 'Bad Name')),
        ('host_eval_case_bad_name', HOST, 'AgentRelease', 0, P('definition.evaluation.cases.0.name', 'has space')),
        ('host_eval_case_empty_input', HOST, 'AgentRelease', 0, P('definition.evaluation.cases.0.input', '')),
        ('host_eval_max_steps_too_high', HOST, 'AgentRelease', 0, P('definition.evaluation.cases.0.expect.maxSteps', 1000)),
        ('host_eval_tool_not_a_name', HOST, 'AgentRelease', 0, P('definition.evaluation.cases.1.expect.toolCalled', 'bad name')),
        ('host_eval_unknown_field', HOST, 'AgentRelease', 0, P('definition.evaluation.extra', True)),
        ('host_run_unknown_purpose', HOST, 'AgentRun', 0, S(purpose='test')),
        # 1.5.0 (D29, D30)
        ('host_routing_strategy_unknown', HOST, 'AgentRelease', 0, P('definition.model.routing.strategy', 'random')),
        ('host_routing_no_candidates', HOST, 'AgentRelease', 0, P('definition.model.routing.candidates', [])),
        ('host_candidate_bad_capability', HOST, 'AgentRelease', 0, P('definition.model.routing.candidates.0.capabilities.0', 'telepathy')),
        ('host_candidate_negative_price', HOST, 'AgentRelease', 0, P('definition.model.routing.candidates.1.costPer1kInput', -1)),
        ('host_candidate_no_model', HOST, 'AgentRelease', 0, P('definition.model.routing.candidates.0', {'provider': 'local'})),
        ('host_a2a_card_not_a_url', HOST, 'AgentRelease', 0, P('definition.subagents.1.a2a.cardUrl', 'carrier-desk')),
        ('host_a2a_embedded_not_a_url', HOST, 'AgentRelease', 0, P('subagents.1.a2a.cardUrl', 'nope')),
        ('host_a2a_unknown_field', HOST, 'AgentRelease', 0, P('definition.subagents.1.a2a.token', 'x')),
        # 1.6.0 (D32)
        ('host_context_on_error_unknown', HOST, 'AgentRelease', 0, P('definition.context.onError', 'retry')),
        ('host_context_kind_unknown', HOST, 'AgentRelease', 0, P('definition.context.kinds.0', 'secrets')),
        ('host_context_budget_too_high', HOST, 'AgentRelease', 0, P('definition.context.maxTokens', 64000)),
        ('host_context_source_not_a_name', HOST, 'AgentRelease', 0, P('definition.context.sources.0', 'Ops Handbook')),
        ('host_context_unknown_field', HOST, 'AgentRelease', 0, P('definition.context.ttl', 30)),
        ('host_run_empty_context_package', HOST, 'AgentRun', 0, S(contextPackageId='')),
        ('host_context_on_stale_unknown', HOST, 'AgentRelease', 0, P('definition.context.onStale', 'ignore')),
    ]

#    idp.telos.cmi: mirrors idp-cmi's TestContractRejectsWhatCMIRejects
CMI = 'idp.telos.cmi'
if CMI in schemas:
    POSITIVE += [
        ('cmi_package_without_scope', CMI, 'ContextPackage', 0, U('scope')),
        ('cmi_record_without_entities_flags', CMI, 'MemoryRecord', 0, U('entities', 'flags')),
        # 1.1.0 (D11-D13)
        ('cmi_item_match_semantic', CMI, 'ContextPackage', 0, P('items.0.match', 'semantic')),
        ('cmi_record_flagged', CMI, 'MemoryRecord', 0, S(flags=['injection:persona_switch'])),
        ('cmi_document_with_entities', CMI, 'KnowledgeDocument', 0, S(entities=[{'type': 'order', 'id': '1001'}])),
        # 1.2.0 (M5: D19, D21, D22, D24)
        ('cmi_record_candidate', CMI, 'MemoryRecord', 0, S(status='candidate', version=1)),
        ('cmi_record_team_scope', CMI, 'MemoryRecord', 0, S(scope={'kind': 'team', 'id': 'us-lane'})),
        ('cmi_item_with_scope', CMI, 'ContextPackage', 0, P('items.1.scope', {'kind': 'project', 'id': 'parcel'})),
        ('cmi_verification_fresh', CMI, 'Verification', 0, S(stale=False, staleItems=[])),
        # 1.3.0 (D25, D26)
        ('cmi_package_without_as_of', CMI, 'ContextPackage', 0, U('asOf')),
        ('cmi_package_with_lag', CMI, 'ContextPackage', 0, S(projectionLag=12.5)),
        # 1.4.0 (D28)
        ('cmi_package_without_omitted', CMI, 'ContextPackage', 0, U('omitted')),
        ('cmi_package_omitted', CMI, 'ContextPackage', 0, S(omitted={'budget': 3, 'flagged': 1, 'candidate': 0, 'disputed': 0, 'forbidden': 0, 'lowSimilarity': 2})),
    ]
    NEGATIVE += [
        ('cmi_package_wrong_schema_version', CMI, 'ContextPackage', 0, S(schemaVersion='idp.cmi.package/v2')),
        ('cmi_package_empty_query', CMI, 'ContextPackage', 0, S(query='')),
        ('cmi_item_unknown_kind', CMI, 'ContextPackage', 0, P('items.0.kind', 'fact')),
        ('cmi_item_bad_trust', CMI, 'ContextPackage', 0, P('items.0.trust', 'verified')),
        ('cmi_budget_over_cap', CMI, 'ContextPackage', 0, P('budget.maxTokens', 64000)),
        ('cmi_record_bad_kind', CMI, 'MemoryRecord', 0, S(kind='fact')),
        ('cmi_record_bad_tag', CMI, 'MemoryRecord', 0, P('tags.0', 'Bad Tag')),
        ('cmi_record_bad_level', CMI, 'MemoryRecord', 0, S(sensitivity='secret')),
        ('cmi_source_bad_kind', CMI, 'Source', 0, S(kind='spreadsheet')),
        ('cmi_document_bad_digest', CMI, 'KnowledgeDocument', 0, S(contentSha256='sha1:abc')),
        # 1.1.0 (D11-D13)
        ('cmi_item_bad_match', CMI, 'ContextPackage', 0, P('items.0.match', 'fuzzy')),
        ('cmi_item_bad_entity_type', CMI, 'ContextPackage', 0, P('items.1.entities.0.type', 'Order')),
        ('cmi_record_bad_entity_type', CMI, 'MemoryRecord', 0, S(entities=[{'type': 'Order', 'id': '1'}])),
        ('cmi_record_entity_empty_id', CMI, 'MemoryRecord', 0, S(entities=[{'type': 'order', 'id': ''}])),
        ('cmi_record_flag_not_a_finding', CMI, 'MemoryRecord', 0, S(flags=['spam'])),
        ('cmi_record_too_many_entities', CMI, 'MemoryRecord', 0, S(entities=[{'type': 'order', 'id': str(i)} for i in range(33)])),
        # 1.2.0 (M5)
        ('cmi_record_version_zero', CMI, 'MemoryRecord', 0, S(version=0)),
        ('cmi_record_unknown_status', CMI, 'MemoryRecord', 0, S(status='quarantined')),
        ('cmi_scope_unknown_kind', CMI, 'MemoryRecord', 0, S(scope={'kind': 'squad', 'id': 'x'})),
        ('cmi_verification_bad_item_status', CMI, 'Verification', 0, P('items.0.status', 'gone')),
        ('cmi_verification_unknown_field', CMI, 'Verification', 0, S(digest='sha256:00')),
        # 1.3.0 (D25, D26)
        ('cmi_as_of_bad_time', CMI, 'ContextPackage', 0, P('asOf.valid', 'yesterday')),
        ('cmi_as_of_unknown_field', CMI, 'ContextPackage', 0, P('asOf.at', '2026-11-04T00:00:00Z')),
        ('cmi_negative_lag', CMI, 'ContextPackage', 0, S(projectionLag=-1)),
        # 1.4.0 (D28)
        ('cmi_omitted_negative', CMI, 'ContextPackage', 0, S(omitted={'budget': -1, 'flagged': 0, 'candidate': 0, 'disputed': 0, 'forbidden': 0, 'lowSimilarity': 0})),
        ('cmi_omitted_missing_reason', CMI, 'ContextPackage', 0, S(omitted={'budget': 1})),
        ('cmi_omitted_unknown_reason', CMI, 'ContextPackage', 0, S(omitted={'budget': 0, 'flagged': 0, 'candidate': 0, 'disputed': 0, 'forbidden': 0, 'lowSimilarity': 0, 'acl': 1})),
    ]

#    idp.telos.workspace: mirrors idp-workspace's TestContractRejectsWhatTheWorkspaceRejects
WS = 'idp.telos.workspace'
if WS in schemas:
    POSITIVE += [
        ('ws_manifest_without_config', WS, 'AppManifest', 0, U('config', 'description')),
        ('ws_item_for_everybody', WS, 'InboxItem', 0, S(assignees={'subjects': [], 'roles': []})),
        ('ws_item_without_due', WS, 'InboxItem', 0, U('dueAt', 'summary')),
        ('ws_context_never_set', WS, 'WorkContext', 0, S(version=0, entities=[]), U('app', 'thread', 'itemId')),
        ('ws_bridge_request_without_params', WS, 'BridgeMessage', 0, U('params')),
        ('ws_bridge_event_without_data', WS, 'BridgeMessage', 4, U('data')),
    ]
    NEGATIVE += [
        ('ws_manifest_wrong_schema_version', WS, 'AppManifest', 0, S(schemaVersion='idp.workspace.app/v2')),
        ('ws_manifest_uppercase_name', WS, 'AppManifest', 0, S(name='Orders')),
        ('ws_manifest_not_semver', WS, 'AppManifest', 0, S(version='1.2')),
        ('ws_manifest_plain_http_entry', WS, 'AppManifest', 0, P('entry.url', 'http://orders.example/')),
        ('ws_manifest_unknown_capability', WS, 'AppManifest', 0, P('capabilities.0', 'storage.write')),
        ('ws_action_input_not_object', WS, 'AppManifest', 0, P('actions.0.input', {'type': 'string'})),
        ('ws_target_unknown_kind', WS, 'AppManifest', 0, P('actions.0.target.kind', 'email')),
        ('ws_target_host_run_without_agent', WS, 'AppManifest', 0, P('actions.0.target', {'kind': 'host.run'})),
        ('ws_target_mixed', WS, 'AppManifest', 0, P('actions.3.target', {'kind': 'webhook', 'url': 'https://x.example/h', 'agent': 'a'})),
        ('ws_target_decision_maybe', WS, 'AppManifest', 0, P('actions.1.target.decision', 'maybe')),
        ('ws_manifest_unknown_field', WS, 'AppManifest', 0, S(extra=True)),
        ('ws_item_unknown_kind', WS, 'InboxItem', 0, S(kind='chat')),
        ('ws_item_bad_ref_system', WS, 'InboxItem', 0, P('ref.system', 'erp')),
        ('ws_item_unknown_status', WS, 'InboxItem', 0, S(status='decided')),
        ('ws_item_version_zero', WS, 'InboxItem', 0, S(version=0)),
        ('ws_item_without_assignees', WS, 'InboxItem', 0, U('assignees')),
        ('ws_release_bad_digest', WS, 'AppRelease', 0, S(digest='sha1:abc')),
        ('ws_invocation_failed_without_error', WS, 'Invocation', 1, U('error')),
        ('ws_invocation_succeeded_with_error', WS, 'Invocation', 0, S(error={'code': 'X', 'message': 'x'})),
        ('ws_bridge_unknown_method', WS, 'BridgeMessage', 0, S(method='token.get')),
        ('ws_bridge_request_with_result', WS, 'BridgeMessage', 0, S(result={})),
        ('ws_bridge_response_with_both', WS, 'BridgeMessage', 1, S(error={'code': 'X', 'message': 'x'})),
        ('ws_bridge_event_with_id', WS, 'BridgeMessage', 4, S(id='1')),
        ('ws_bridge_other_protocol', WS, 'BridgeMessage', 4, S(protocol='idp.bridge/v2')),
    ]

for expect, cases in ((True, POSITIVE), (False, NEGATIVE)):
    for name, ns, definition, index, *mutations in cases:
        x = pick(ns, definition, index)
        for mutate in mutations:
            mutate(x)
        check(name, ns, definition, x, expect)


# 3. Structural invariants
def walk(node, visit, path=()):
    if isinstance(node, dict):
        visit(node, path)
        for k, v in node.items():
            walk(v, visit, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, visit, path + (i,))


missing_meta = [f'{ns}:{name}' for ns, s in schemas.items() for name, d in s['$defs'].items()
                if not all(d.get(k) for k in ('title', 'description', OWNER_KEY))]
invariant('every_definition_has_title_description_owner', not missing_meta, missing_meta)

foreign = [f'{ns}:{name}' for ns, s in schemas.items() for name, d in s['$defs'].items() if d.get(OWNER_KEY) != ns]
invariant('every_definition_is_owned_by_its_namespace', not foreign, foreign)

unresolved = []


def resolve_refs(node, _path, base):
    ref = node.get('$ref')
    if isinstance(ref, str):
        try:
            registry.resolver(base_uri=base).lookup(ref)
        except Exception as e:  # noqa: BLE001 - any resolution failure is a finding
            unresolved.append(f'{base} {ref}: {type(e).__name__}')


for s in schemas.values():
    walk(s, lambda n, p, base=s['$id']: resolve_refs(n, p, base))
invariant('all_schema_refs_resolve', not unresolved, unresolved)

no_example = [f'{ns}:{d}' for ns in schemas for d in roots[ns] if d not in {d for d, _ in examples[ns]}]
invariant('every_root_document_has_an_example', not no_example, no_example)

mcp_open = {n for n, d in schemas[MCP]['$defs'].items() if d.get('additionalProperties') is not False}
invariant('mcp_only_pinned_mcp_objects_are_open', mcp_open == {'Tool', 'Prompt', 'Resource', 'ResourceTemplate'}, sorted(mcp_open))
invariant('mcp_root_documents', roots[MCP] == {'Release', 'Envelope', 'InvocationRecord'}, sorted(roots[MCP]))
if HOST in schemas:
    invariant('host_root_documents', roots[HOST] == {'AgentRelease', 'SkillRelease', 'Envelope', 'AgentRun', 'RunOptions'}, sorted(roots[HOST]))
    host_open = [n for n, d in schemas[HOST]['$defs'].items() if d.get('additionalProperties') is not False]
    invariant('host_definitions_are_closed', not host_open, host_open)

if WS in schemas:
    invariant('workspace_root_documents', roots[WS] == {'Workspace', 'AppManifest', 'AppRelease', 'Installation', 'InboxItem', 'WorkContext', 'Invocation', 'BridgeMessage', 'Problem'}, sorted(roots[WS]))
    ws_open = [n for n, d in schemas[WS]['$defs'].items() if d.get('additionalProperties') is not False]
    invariant('workspace_definitions_are_closed', not ws_open, ws_open)

# 3b. Minor versions only add: against every previous version whose files are still in the tree,
#     the current schema keeps every definition, never adds a required property to one, never
#     removes a property, and never drops a root document.
for ns, n in NAMESPACES.items():
    for prev in n.get('previous', []):
        if not prev.get('schema') or not (ROOT / prev['schema']).exists():
            continue
        if prev['version'].split('.')[0] != n['version'].split('.')[0]:
            continue  # a major version may break compatibility
        old = load(prev['schema'])
        new = schemas[ns]
        not_additive = []
        for name, od in old['$defs'].items():
            nd = new['$defs'].get(name)
            if nd is None:
                not_additive.append(f'{name}: removed')
                continue
            if not set(nd.get('required', [])) <= set(od.get('required', [])):
                not_additive.append(f'{name}: required property added')
            if not set(od.get('properties', {})) <= set(nd.get('properties', {})):
                not_additive.append(f'{name}: property removed')
        old_roots = {r['$ref'].split('/')[-1] for r in old.get('oneOf', [])}
        if not old_roots <= roots[ns]:
            not_additive.append('root document removed')
        invariant(f'{ns.replace(".", "_")}_{n["version"].replace(".", "_")}_is_additive_over_{prev["version"].replace(".", "_")}', not not_additive, not_additive)

# 4. Registry
entries = {}
for e in reg['objects']:
    entries.setdefault((e['owner'], e['name']), []).append(e)
dupes = [f'{o}:{n}' for (o, n), es in entries.items() if len(es) > 1]
invariant('registry_names_unique_per_namespace', not dupes, dupes)
reg_mismatch = []
for ns, s in schemas.items():
    for name in s['$defs']:
        e = (entries.get((ns, name)) or [{}])[0]
        if e.get('file') != NAMESPACES[ns]['schema'] or e.get('status') != 'PUBLISHED' or e.get('version') != NAMESPACES[ns]['version'] \
                or e.get('role') not in ('CONTRACT', 'HELPER') or (e['role'] == 'CONTRACT') != (name in roots[ns]):
            reg_mismatch.append(f'{ns}:{name}')
invariant('registry_matches_published_schemas', not reg_mismatch, reg_mismatch)
dangling = [f'{e["owner"]}:{e["name"]}' for e in reg['objects'] if e['name'] not in schemas.get(e['owner'], {'$defs': {}})['$defs']]
invariant('registry_entries_are_defined', not dangling, dangling)
ids_ok = all(n['id'] == schemas[ns]['$id'] for ns, n in NAMESPACES.items())
invariant('registry_namespace_ids_match', ids_ok)
bad_status = [f'{ns}:{n.get("status")}' for ns, n in reg['namespaces'].items()
              if n.get('status') not in ('PUBLISHED', 'DRAFT') or (n['status'] == 'PUBLISHED') != bool(n.get('schema'))]
invariant('registry_namespace_statuses', not bad_status, bad_status)
no_producer = [ns for ns in NAMESPACES if not NAMESPACES[ns].get('producer', {}).get('consistency_test')]
invariant('every_published_namespace_names_its_producer_and_consistency_test', not no_producer, no_producer)

# 5. Producer sync: a namespace is published only as a byte-identical copy of the producer's files.
producer_sync = 'PASSED'
for ns, n in NAMESPACES.items():
    producer = n['producer']
    src = PRODUCER_ROOT / producer['repository'] / producer['path']
    if not src.is_dir():
        producer_sync = 'NOT_RUN_PRODUCER_MISSING'
        print(f'producer checkout for {ns} not found at {src}; pass --producer-root', file=sys.stderr)
        continue
    diverged = []
    for rel in (n['schema'], n['examples']):
        published = (ROOT / rel).read_bytes()
        source = src / Path(rel).name
        if not source.exists() or hashlib.sha256(published).digest() != hashlib.sha256(source.read_bytes()).digest():
            diverged.append(f'{rel} != {source}')
    invariant(f'{ns.replace(".", "_")}_published_matches_producer', not diverged, diverged)

# 6. Report
cases_ok = all(r['passed'] for r in results)
invariants_ok = all(v['passed'] for v in invariants.values())
report = {
    'scope': 'FILE_LEVEL_ONLY',
    'checked_on': datetime.date.today().isoformat(),
    'validator': 'jsonschema ' + importlib.metadata.version('jsonschema'),
    'referencing': importlib.metadata.version('referencing'),
    'draft': '2020-12',
    'format_checker_enabled': True,
    'namespaces': {ns: {'id': s['$id'], 'version': NAMESPACES[ns]['version'], 'definitions': len(s['$defs'])} for ns, s in schemas.items()},
    'positive_cases': sum(r['expected_valid'] for r in results),
    'negative_cases': sum(not r['expected_valid'] for r in results),
    'passed': sum(r['passed'] for r in results),
    'total': len(results),
    'invariants_passed': sum(v['passed'] for v in invariants.values()),
    'invariants_total': len(invariants),
    'producer_sync': producer_sync,
    'invariants': invariants,
    'cases': results,
}
REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k not in ('cases', 'invariants')}, ensure_ascii=False))
if not (cases_ok and invariants_ok):
    raise SystemExit(1)
raise SystemExit(0 if producer_sync == 'PASSED' else 2)
