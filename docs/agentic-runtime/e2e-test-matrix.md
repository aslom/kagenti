# OpenShell E2E Test Matrix

> Back to [main doc](openshell-integration.md)

## Agent Taxonomy

Every test category covers ALL agent types. Unsupported combinations are
explicitly skipped with documented reasons and TODOs.

| Agent ID | Type | Protocol | LLM | Supervisor | Skill Support |
|----------|------|----------|-----|------------|---------------|
| `weather_agent` | Custom A2A | A2A JSON-RPC | No | No | N/A (no LLM) |
| `adk_agent` | Custom A2A | A2A JSON-RPC | LiteMaaS | No | Via LLM tool calling |
| `claude_sdk_agent` | Custom A2A | A2A JSON-RPC | LiteMaaS | No | Via LLM prompt injection |
| `weather_supervised` | Custom A2A | kubectl exec | No | Yes | N/A (no LLM) |
| `openshell_claude` | Builtin sandbox | kubectl exec | Anthropic | Yes | Native `.claude/skills/` |
| `openshell_opencode` | Builtin sandbox | kubectl exec | OpenAI-compat | Yes | Via tool/prompt system |
| `openshell_generic` | Builtin sandbox | kubectl exec | N/A | Yes | No agent |

## Phase 1 PoC Test Results

| Platform | Total | Passed | Failed | Skipped | Notes |
|----------|-------|--------|--------|---------|-------|
| Kind | 117 | 78-82 | 0-5 | 34 | Failures are rollout timing; 0 on clean runs |
| HyperShift | 117 | 75-76 | 0-4 | 38 | Same rollout timing issue |

Infrastructure:
- LiteLLM model proxy with aliases (gpt-4o-mini → llama-scout-17b)
- HITL OPA egress blocking tests (python3 urllib, not curl)
- PVC workspace persistence tests
- Sandbox creation for Claude + OpenCode
- TCP readiness probes (Istio ambient mTLS compatible)

## Result Legend

- **PASS** (✅) — test runs and passes
- **FAIL** — test runs but assertion fails (intermittent = rollout timing)
- **SKIP** (⏭️) — test skips with documented reason
- **—** — test not applicable for this agent type

## Skill Execution Matrix (Kind, fresh cluster)

| Skill | adk | claude_sdk | weather | supervised | os_claude | os_opencode | os_generic |
|-------|-----|-----------|---------|-----------|----------|------------|-----------|
| PR review | ✅ | ✅ | ⏭️ no LLM | ⏭️ no LLM | ⏭️ Anthropic key | ⏭️ /v1/responses API | ⏭️ no agent |
| RCA | ✅ | ✅ | ⏭️ no LLM | ⏭️ no LLM | ⏭️ Anthropic key | ⏭️ /v1/responses API | ⏭️ no agent |
| Security review | ✅ | ✅ | ⏭️ no LLM | ⏭️ no LLM | ⏭️ Anthropic key | ⏭️ /v1/responses API | ⏭️ no agent |
| Code generation | — | ✅ | — | — | — | — | — |
| Real GitHub PR | ✅ | ✅ | — | — | ⏭️ Anthropic key | ⏭️ /v1/responses API | — |
| RCA CI logs | — | ✅ | — | — | — | — | — |

**Skip reasons:**
- `no LLM` — agent has no LLM capability (by design)
- `no agent` — generic sandbox has no agent CLI
- `Anthropic key` — Claude Code requires real Anthropic API key (Phase 2)
- `/v1/responses API` — OpenCode uses OpenAI Responses API which LiteLLM doesn't proxy (needs LiteLLM update or OpenCode flag)

## A2A Connectivity Matrix

Tests basic message/send and agent card discovery. Only custom A2A agents
are tested — builtin sandboxes (os_claude, os_opencode, os_generic) don't
expose A2A endpoints (they use SSH/exec instead).

| Test | weather_agent | adk_agent | claude_sdk_agent | weather_supervised | os_claude | os_opencode | os_generic |
|------|--------------|-----------|-----------------|-------------------|----------|------------|-----------|
| A2A hello | ✅ | ✅ | ✅ | ✅ (exec) | — | — | — |
| Agent card | ✅ | ✅ | ✅ | ⏭️ netns | — | — | — |

**Why no builtin sandboxes:** Builtin sandboxes don't run A2A servers. They are
CLI tools (Claude Code, OpenCode) accessed via SSH or kubectl exec, not HTTP.

## Multi-Turn Conversation Matrix

Tests sequential A2A messages and context persistence. Only A2A agents
can be tested via the A2A protocol. Builtin sandboxes would need the
ExecSandbox gRPC adapter for multi-turn (Phase 2).

| Test | weather_agent | adk_agent | claude_sdk_agent | weather_supervised | os_claude | os_opencode | os_generic |
|------|--------------|-----------|-----------------|-------------------|----------|------------|-----------|
| 3 sequential messages | ✅ | ✅ | ✅ | ✅ (exec) | — | — | — |
| Context isolation | ✅ | ✅ | ✅ | ⏭️ netns | — | — | — |
| Context continuity | ⏭️ stateless | ⏭️ ADK gap | ⏭️ stateless | ⏭️ Phase 2 | — | — | — |

**Why no builtin sandboxes:** Multi-turn requires sending multiple messages.
Builtin sandboxes use terminal sessions (persistent by nature) or need the
ExecSandbox gRPC adapter to simulate multi-turn.

## Credential Security Matrix

Tests secret delivery and policy mounting. All custom A2A agents are tested.
Builtin sandboxes are not tested because they use the OpenShell gateway's
provider injection mechanism (different credential path — see Q2.3).

| Test | weather_agent | adk_agent | claude_sdk_agent | weather_supervised | os_claude | os_opencode | os_generic |
|------|--------------|-----------|-----------------|-------------------|----------|------------|-----------|
| secretKeyRef delivery | — | ✅ | ✅ | — | — | — | — |
| No hardcoded keys | ✅ | ✅ | ✅ | ✅ | — | — | — |
| No K8s token leak | ✅ | ✅ | ✅ | ✅ | — | — | — |
| Policy file mounted | ✅ | ✅ | ✅ | ✅ | — | — | — |
| Policy valid YAML | ✅ | ✅ | ✅ | ✅ | — | — | — |

**Why only custom agents:** Builtin sandboxes get credentials from the OpenShell
gateway via `GetProviderEnvironment` gRPC, not from K8s Secrets.
**Why weather/supervised have no secretKeyRef:** They have no LLM API key.

## Supervisor Enforcement Matrix

Tests OpenShell supervisor security layers. Only `weather_supervised` has
the supervisor deployed. Other custom A2A agents are Tier 3 (no supervisor).
Builtin sandboxes have the supervisor but are tested via sandbox lifecycle
tests, not enforcement-specific tests.

| Test | weather_agent | adk_agent | claude_sdk_agent | weather_supervised | os_claude | os_opencode | os_generic |
|------|--------------|-----------|-----------------|-------------------|----------|------------|-----------|
| Landlock applied | — | — | — | ✅ | — | — | — |
| Landlock ABI V3 | — | — | — | ✅ | — | — | — |
| Read-only paths | — | — | — | ✅ | — | — | — |
| Read-write paths | — | — | — | ✅ | — | — | — |
| Netns created | — | — | — | ✅ | — | — | — |
| OPA proxy | — | — | — | ✅ | — | — | — |
| Seccomp | — | — | — | ✅ | — | — | — |
| TLS termination | — | — | — | ✅ | — | — | — |

**Why only weather_supervised:** It's the only Tier 2 agent (Deployment + supervisor).
Other agents are Tier 3 (no supervisor). When agents upgrade to Tier 2,
these tests will cover them too.

## Workspace Persistence Matrix

Tests PVC-backed workspace for builtin sandboxes. Custom A2A agents don't
have persistent workspaces (they're stateless services).

| Test | weather_agent | adk_agent | claude_sdk_agent | weather_supervised | os_claude | os_opencode | os_generic |
|------|--------------|-----------|-----------------|-------------------|----------|------------|-----------|
| Session to PVC | — | — | — | — | ✅ | ✅ | ✅ |
| PVC survives deletion | — | — | — | — | — | — | ✅ |
| Sandbox CR created | — | — | — | — | ✅ | ✅ | ✅ |

**Why only builtin sandboxes (current):** Custom A2A agents are Deployment-backed
stateless services and don't have workspace PVCs in this PoC. Builtin sandboxes
are the ones with persistent workspaces for code, configs, and sessions.

**Future integration:** The Kagenti sandboxing PR adds workspace PVCs to custom
agents (Tier 2/3 Deployments with PVC mounts). When deployed, these tests will
extend to cover custom agent workspace persistence — enabling session state,
agent memory, and file persistence across pod restarts for all agent types.

## HITL Policy Matrix

Tests OPA egress enforcement. Only `weather_supervised` has the supervisor
with OPA proxy active. When other agents upgrade to Tier 2, HITL will apply.

| Test | weather_agent | adk_agent | claude_sdk_agent | weather_supervised | os_claude | os_opencode | os_generic |
|------|--------------|-----------|-----------------|-------------------|----------|------------|-----------|
| OPA denies egress | — | — | — | ✅ | — | — | — |
| OPA allows egress | — | — | — | ⏭️ DNS | — | — | — |
| Denial logged | — | — | — | ⏭️ log fmt | — | — | — |

**Why only weather_supervised:** Only Tier 2 agents have the supervisor's
OPA proxy for egress enforcement.

## Test Categories

### 1. Platform Health

Verifies gateway, operator, and all agent pods are running and healthy.

| Test | weather | adk | claude_sdk | supervised | openshell_* | Notes |
|------|---------|-----|------------|------------|-------------|-------|
| gateway_pod_running | - | - | - | - | - | Checks openshell-system |
| gateway_containers_ready | - | - | - | - | - | All containers in gateway |
| operator_pod_running | - | - | - | - | - | SKIP on HyperShift if not installed |
| all_agent_pods_exist | PASS | PASS | PASS | PASS | - | Dynamic discovery |
| all_agent_pods_running | PASS | PASS | PASS | PASS | - | Excludes build pods |
| agent_deployments_ready | PASS | PASS | PASS | PASS | - | All replicas ready |
| no_crashlooping_agents | PASS | PASS | PASS | PASS | - | Zero CrashLoopBackOff |

### 2. Credential Isolation

Verifies secrets are delivered via K8s secretKeyRef, not hardcoded. Parametrized
across all 4 custom agents.

| Test | weather | adk | claude_sdk | supervised | Notes |
|------|---------|-----|------------|------------|-------|
| api_key_from_secret_ref | - | PASS | PASS | - | Only LLM agents have API keys |
| no_literal_api_keys | PASS | PASS | PASS | PASS | No `sk-` patterns in YAML |
| no_kubernetes_token_exposed | PASS | PASS | PASS | PASS | SA token not in env |
| policy_file_exists | PASS | PASS | PASS | PASS | `/etc/openshell/policy.yaml` |
| policy_is_valid_yaml | PASS | PASS | PASS | PASS | Has version + policy sections |
| supervisor_entrypoint | SKIP | SKIP | SKIP | PASS | Only supervised has supervisor as PID 1 |
| placeholder_tokens | - | SKIP | SKIP | - | TODO: supervisor credential injection |

### 3. A2A Agent Conversations

Direct A2A JSON-RPC conversations with each agent. LLM-gated tests require
`OPENSHELL_LLM_AVAILABLE=true`.

| Test | weather | adk | claude_sdk | Notes |
|------|---------|-----|------------|-------|
| hello | - | PASS | PASS | Basic A2A handshake |
| weather_query_london | PASS | - | - | MCP tool call |
| weather_query_multi_city | PASS | - | - | Multi-tool call |
| pr_review (LLM) | - | PASS | - | ADK tool + LLM |
| code_review (LLM) | - | - | PASS | Claude SDK + LLM |

### 4. Multi-Turn Conversations

Tests sequential messages across all A2A agent types. Context continuity
tests document which agents preserve conversation state.

| Test | weather | adk | claude_sdk | supervised | Notes |
|------|---------|-----|------------|------------|-------|
| responds_to_3_turns | PASS | PASS | PASS | PASS (exec) | All agents handle sequential messages |
| context_isolation | PASS | PASS | PASS | SKIP (netns) | Independent conversations don't share state |
| context_continuity | SKIP | SKIP | SKIP | SKIP | No agent preserves contextId yet |

**Skip reasons for context_continuity:**
- `weather_agent`: Stateless, no contextId returned. TODO: Kagenti backend session store.
- `adk_agent`: Returns contextId but `to_a2a()` creates new one per request. TODO: upstream ADK PR.
- `claude_sdk_agent`: Stateless, no contextId. TODO: Kagenti backend session store.
- `weather_supervised`: Netns blocks port-forward. TODO: ExecSandbox gRPC adapter.

### 5. Conversation Survives Restart

Scale agent to 0, back to 1, try to continue conversation. Tests whether
context persists across pod restarts.

| Test | weather | adk | claude_sdk | supervised | Notes |
|------|---------|-----|------------|------------|-------|
| multiturn_across_restart | SKIP | SKIP | SKIP | SKIP | Responds but context lost |
| pod_uid_changes | PASS | PASS | PASS | PASS | Confirms new pod created |

**All `multiturn_across_restart` skip** because no agent currently preserves
context across restarts. This is by design — context should live in the
Kagenti backend, not in the agent.

**When Kagenti backend session store is integrated, these tests will:**
1. Pre-populate conversation history in PostgreSQL
2. Scale agent to 0, then back to 1
3. Send follow-up message (backend includes context from DB)
4. Verify agent responds with awareness of previous conversation

### 6. PVC Workspace Persistence

Creates sandboxes with PVC-backed `/workspace`, writes session state, verifies
data persists. Parametrized across all builtin sandbox types.

| Test | openshell_generic | openshell_claude | openshell_opencode | Notes |
|------|-------------------|------------------|--------------------|-------|
| session_written_to_pvc | PASS | PASS | PASS | Session file persisted |
| pvc_survives_deletion | PASS | PASS | PASS | PVC independent of CR |

### 7. Sandbox Status Observability

A2A equivalent of `openshell term`. Verifies all status data is queryable
via K8s API (same data the Kagenti UI renders).

| Test | Notes |
|------|-------|
| gateway_status_queryable | StatefulSet replicas, readiness |
| agent_deployments_status_queryable | Replica counts, conditions |
| agent_pods_status_queryable | Phase, containerStatuses, restartCount |
| sandbox_cr_status_queryable | Sandbox CRD list operation |
| gateway_logs_accessible | Gateway logs readable |

### 8. Agent Service Persistence

A2A equivalent of session reconnect. Verifies agents remain available across
multiple independent HTTP connections.

| Test | Notes |
|------|-------|
| responds_across_connections | 3 requests, fresh TCP each time |
| stable_after_delay | Responds after 5s idle |
| pod_not_restarted_during_requests | restartCount unchanged |

### 9. Supervisor Enforcement

Verifies OpenShell security layers on `weather_supervised` agent.

| Test | Result | What it verifies |
|------|--------|------------------|
| landlock_applied_in_logs | PASS | CONFIG:APPLYING + rules_applied:14+ |
| landlock_abi_version | PASS | ABI V2+ |
| read_only_paths_configured | PASS | /usr, /etc in policy |
| read_write_paths_configured | PASS | /tmp, /app in policy |
| netns_created_in_logs | PASS | CONFIG:CREATING + 10.200.0.1/10.200.0.2 |
| opa_proxy_listening | PASS | NET:LISTEN + 10.200.0.1:3128 |
| netns_name_in_logs | PASS | ns:sandbox-{hex} |
| seccomp_not_disabled | PASS | No Unconfined in pod spec |
| opa_policy_loaded | PASS | CONFIG:LOADING + sandbox-policy.rego |
| policy_has_network_rules | PASS | network_policies + endpoints in YAML |
| rego_file_mounted | PASS | /etc/openshell/sandbox-policy.rego exists |
| tls_termination_enabled | PASS | TLS termination + ephemeral CA |

### 10. Skill Discovery & Execution

Tests kagenti skill loading and LLM-backed execution.

| Test | Agent | Result | What it verifies |
|------|-------|--------|------------------|
| create_skills_configmap | ALL | PASS | ConfigMap created in team1 |
| skills_configmap_has_index | ALL | PASS | JSON index with skill list |
| skills_include_review | ALL | PASS | Review skill in index |
| weather_agent_lists_skills | weather | PASS | Agent card includes skills |
| claude_sdk_has_code_review | claude_sdk | PASS | Agent card has code_review |
| claude_sdk_pr_review_skill | claude_sdk | PASS | Real LLM PR review |
| adk_pr_review_skill | adk | PASS | Real LLM PR review |
| claude_sdk_rca_skill | claude_sdk | PASS | Real LLM RCA analysis |
| claude_sdk_security_review | claude_sdk | PASS | Real LLM security review |
| review_real_github_pr | claude_sdk | PASS | Real GitHub PR #1300 diff |
| rca_style_log_analysis | claude_sdk | PASS | Real LLM CI log analysis |

### 11. Builtin Sandboxes

Tests OpenShell base image sandbox creation via Sandbox CRs.

| Test | Result | Notes |
|------|--------|-------|
| create_sandbox_cr | PASS | Sandbox CR accepted by API |
| gateway_sees_sandbox | PASS | Gateway logs show processing |
| base_image_cli_check | PASS (Kind) / SKIP (HyperShift) | CLI availability in base image |
| claude_sandbox_responds | PASS | Claude sandbox created |
| opencode_sandbox_responds | PASS | OpenCode sandbox created |
| codex_sandbox | SKIP | Requires real OpenAI key |
| copilot_sandbox | SKIP | Requires GitHub subscription |

### 12. Sandbox Lifecycle

Tests Sandbox CR CRUD operations.

| Test | Result | Notes |
|------|--------|-------|
| list_sandboxes | PASS | List operation succeeds |
| create_sandbox | PASS | CR created and verified |
| delete_sandbox | PASS | CR deleted cleanly |
| gateway_processes_sandbox | PASS | Gateway logs confirm processing |

---

## Future Tests (Enabled by Kagenti Backend Integration)

These tests become possible when the Kagenti backend is wired to the
OpenShell gateway and manages agent sessions.

### Session Management via Backend

| Test | Agents | What it validates | Depends on |
|------|--------|-------------------|------------|
| backend_creates_a2a_session | ALL A2A | Backend creates session in PostgreSQL | Backend A2A adapter |
| backend_stores_conversation | ALL A2A | Turn history persisted in DB | Session store |
| backend_restores_context_after_restart | ALL A2A | Context survives pod restart via DB | Session restore logic |
| backend_creates_sandbox_session | openshell_* | Backend creates sandbox via gRPC | ExecSandbox adapter |
| backend_exec_in_sandbox | openshell_* | Backend sends prompt via ExecSandbox | ExecSandbox adapter |

### Skill Execution Matrix (All Agents)

| Test | weather | adk | claude_sdk | supervised | openshell_claude | openshell_opencode | Notes |
|------|---------|-----|------------|------------|-----------------|-------------------|-------|
| load_skill_from_configmap | N/A | PASS | PASS | N/A | TODO | TODO | Skill markdown loaded |
| execute_pr_review_skill | N/A | PASS | PASS | N/A | TODO | TODO | LLM follows skill instructions |
| execute_rca_skill | N/A | TODO | PASS | N/A | TODO | TODO | RCA methodology followed |
| execute_security_review | N/A | TODO | PASS | N/A | TODO | TODO | Security issues identified |
| native_claude_skill | N/A | N/A | N/A | N/A | TODO | N/A | Claude Code reads .claude/skills/ directly |

**`openshell_claude` is the highest-value target** for skill execution because
Claude Code natively reads `.claude/skills/` — no prompt injection needed.
This requires a real Anthropic API key (Phase 2 provider integration).

### PVC + Session Restore

| Test | Agents | What it validates |
|------|--------|-------------------|
| workspace_survives_sandbox_restart | openshell_* | PVC data persists across sandbox recreate |
| backend_resumes_from_pvc | openshell_* | Backend loads workspace state + DB history |
| file_browser_reads_workspace | openshell_* | Kagenti UI FileBrowser can browse PVC |

### Observability Integration

| Test | Agents | What it validates |
|------|--------|-------------------|
| llm_usage_tracked | adk, claude_sdk | Token counts appear in LlmUsagePanel |
| otel_spans_exported | ALL | Traces visible in Phoenix |
| supervisor_events_in_otel | supervised, openshell_* | Landlock/netns events exported |
