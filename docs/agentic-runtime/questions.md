# Pending Questions and Investigation Paths

> Back to [main doc](openshell-integration.md)
>
> Sources: [Paolo's integration proposal](https://github.com/kagenti/kagenti/pull/1300),
> PoC implementation (PR #1300), alignment analysis, codebase research

## Status Legend

- **ANSWERED** — Our PoC has a concrete answer with evidence
- **OPEN** — Needs discussion with OpenShell/Kagenti teams
- **INVESTIGATING** — Research in progress, partial answers
- **BLOCKED** — Needs upstream OpenShell changes
- **TESTED** — Covered by E2E tests with pass/skip status

---

## 1. Agent Interaction Model

### Q1.1: How do long-running A2A agents fit the OpenShell model?

**Status:** ANSWERED

The proposal describes only CLI+SSH interactive sessions. Our PoC demonstrates
that A2A agents (Deployments + Services) work alongside the OpenShell gateway
without requiring the CLI or SSH tunnel.

**Answer:** Both models coexist. Custom A2A agents use Kagenti's Deployment model.
Builtin sandboxes use OpenShell's Sandbox CR model. The Kagenti backend is the
unified session manager for both.

**Test coverage:** 8 tests in `test_02_a2a_connectivity.py` (all pass)

### Q1.2: How should the Kagenti UI expose sandbox sessions?

**Status:** OPEN

**Candidates:**
1. **A2A-first (recommended):** All agent interaction via A2A protocol + AgentChat UI.
   CLI+SSH as escape hatch for debugging. Simplest to implement.
2. **Embedded terminal:** xterm.js in the UI connected via WebSocket → SSH tunnel.
   Requires gateway ingress (L4 passthrough) and browser-to-SSH bridging.
3. **Hybrid:** UI for lifecycle (create/destroy), A2A for programmatic, SSH for interactive.

**Impact on tests:** None immediate. Phase 2 UI integration work.

### Q1.3: How does `openshell sandbox sessions` interact with Kagenti session management?

**Status:** OPEN

OpenShell gateway tracks sessions in its own DB (SQLite/Postgres). Kagenti backend
has a separate session store in PostgreSQL. These are independent systems.

**Candidates:**
1. **Single session store:** Kagenti backend IS the session store. Gateway delegates via gRPC.
2. **Dual stores with sync:** Both store sessions. A reconciliation loop syncs state.
3. **Gateway sessions for SSH, backend sessions for A2A:** Each system owns its protocol.

**Impact on tests:** `test_multiturn_context_continuity` (4 skips) — backend session store
would enable context persistence without agent-side changes.

### Q1.4: How does multi-agent orchestration work within a team namespace?

**Status:** OPEN

Our PoC deploys 4 agents in `team1`. The proposal's model is one agent per sandbox.

**Candidates:**
1. **Independent agents:** Each agent is a separate Deployment/Sandbox. Backend orchestrates.
2. **Agent delegation:** One agent delegates sub-tasks to others via A2A.
3. **Shared sandbox:** Multiple agents in one sandbox pod (not currently supported).

---

## 2. Security and Privileges

### Q2.1: What is the minimum privilege set for the supervisor?

**Status:** ANSWERED

Research confirms: `CAP_NET_ADMIN` + `CAP_SYS_ADMIN` + `CAP_SYS_PTRACE` (not `privileged: true`).
The supervisor's seccomp filter blocks mount syscalls. Our Kind-specific need for
`privileged: true` was due to `mount --make-shared` in the container setup, not the
supervisor binary itself.

**Answer:** Custom SCC with specific capabilities. See `sandboxing-layers.md`.

**TODO:** Test `weather-agent-supervised` with reduced capabilities on both platforms.

**Impact on tests:** Would validate supervisor enforcement without `privileged: true`.

### Q2.2: Has the proposed SCC been tested on OCP with SELinux enforcing?

**Status:** TESTED (partial)

Our PoC runs on HyperShift (OCP 4.20) with SELinux enforcing. 75 tests pass, 0 fail.
However, we use `privileged: true` SCC, not the reduced capability set.

**TODO:** Test with `allowPrivilegedContainer: false` + specific capabilities on OCP.

### Q2.3: K8s Secrets (Kagenti) vs placeholder tokens (OpenShell)?

**Status:** OPEN

Two credential models exist:
- **Kagenti:** API keys in K8s Secrets, injected via `secretKeyRef`
- **OpenShell:** Placeholder tokens (`openshell:resolve:env:*`) resolved by supervisor proxy

**Candidates:**
1. **K8s Secrets for custom agents, placeholders for builtin sandboxes:** Each model
   where it's strongest. Custom agents don't have supervisor; builtin sandboxes do.
2. **Placeholders everywhere:** All agents use supervisor proxy for credential resolution.
   Requires supervisor on every agent (Phase 3).
3. **K8s Secrets everywhere:** Simpler but loses zero-secret isolation benefit.

**Impact on tests:** `test_credential__placeholder_tokens` (2 skips) — needs supervisor integration.

### Q2.4: How should live egress blocking be validated?

**Status:** TESTED (new)

Our `test_09_hitl_policy.py` tests OPA egress blocking via `kubectl exec` into
the supervised agent. Three tests: deny unauthorized, allow authorized, log denials.

**Hurdle:** `curl` not available in supervised agent pod. Fixed by using python3 urllib.

---

## 3. LLM Integration and Inference Routing

### Q3.1: OpenShell inference router vs Kagenti LiteLLM — which is canonical?

**Status:** OPEN

Both route LLM traffic. OpenShell's inference router strips credentials and injects
backend keys. Kagenti's LiteLLM provides model routing, virtual keys, and budget tracking.

**Candidates:**
1. **LiteLLM as backend for inference router:** OpenShell proxy routes to LiteLLM endpoint.
   Best of both: zero-secret isolation + budget tracking.
2. **LiteLLM replaces inference router:** Agents call LiteLLM directly (current PoC model).
   Simpler but loses supervisor-level credential stripping.
3. **Inference router for builtin, LiteLLM for custom:** Each where appropriate.

**Impact on tests:** Affects how we configure LLM for `openshell_opencode` tests.

### Q3.2: Per-session budget enforcement across both systems?

**Status:** OPEN

LiteLLM has per-key budgets. Budget Proxy has per-session budgets. OpenShell's
inference router has no budget concept.

**Candidates:**
1. **LiteLLM virtual keys per session:** Create a unique LiteLLM key per conversation.
2. **Budget Proxy per sandbox:** Each sandbox gets its own Budget Proxy instance.
3. **Gateway-level quota:** Add budget tracking to OpenShell gateway (upstream contribution).

### Q3.3: Can openshell_opencode use LiteMaaS for skill execution?

**Status:** INVESTIGATING

OpenCode uses OpenAI-compatible format. LiteMaaS provides that. The gateway needs
`OPENAI_API_KEY` + `OPENAI_BASE_URL` env vars (already configured by fulltest script).

**Hurdle:** The builtin sandbox runs OpenCode CLI which reads provider config from the
gateway's credential store (not env vars directly). Need to verify the gateway's
provider auto-discovery actually injects credentials into sandbox pods via
`GetProviderEnvironment` gRPC.

**Impact on tests:** Would enable 3 `openshell_opencode` skill execution tests (currently skip).
Test: `test_pr_review__openshell_opencode__litemaas_provider`

---

## 4. Session Persistence and Context

### Q4.1: How should multi-turn context work for A2A agents?

**Status:** OPEN — highest priority for test coverage

No A2A agent currently preserves context across requests. The ADK agent returns
`contextId` but creates a new one per request (upstream ADK gap).

**Candidates:**
1. **Backend-managed context (recommended):** Kagenti backend stores history in PostgreSQL.
   Each turn, backend sends full history as part of the A2A request. Agent is stateless.
   - Investigation: Implement in `kagenti/backend/app/services/session_db.py`
   - Effort: Medium (backend code + A2A adapter)
2. **Agent-side PVC session store:** Agent reads/writes session state to PVC.
   - Investigation: Add PVC to ADK/Claude SDK deployments, implement checkpoint/resume
   - Effort: High (agent code changes per framework)
3. **Upstream ADK contextId fix:** Wait for Google ADK to support client-sent contextId.
   - Investigation: File issue on google/adk-python, track PR
   - Effort: Zero (waiting)

**Impact on tests:** Would enable 4 `test_context_continuity` tests (currently skip).

### Q4.2: Do Claude Code and OpenCode store sessions on disk that can be resumed?

**Status:** ANSWERED

Yes. Claude Code stores in `~/.claude/projects/<hash>/` (JSONL transcripts).
OpenCode stores in `~/.opencode/`. In the sandbox, these paths are on the
PVC-mounted `/sandbox` directory. The data survives pod restart.

However, **session resume is not automatic** — the agent CLI loads prior sessions
when opening a project but doesn't automatically continue a previous conversation.
The user must explicitly reference prior context.

**Impact on tests:** `test_resume__generic_sandbox__write_delete_recreate_read` tests
file persistence. A conversation-level resume test needs the agent CLI to actually
process prior session data — requires ExecSandbox gRPC adapter (Phase 2).

### Q4.3: How does the ExecSandbox gRPC work for sending prompts to builtin agents?

**Status:** ANSWERED

The gateway's `ExecSandbox` RPC supports:
- Command + args execution
- Optional stdin payload
- Streaming stdout/stderr/exit response
- Environment overrides
- Timeout configuration
- PTY support

The Kagenti backend would call `ExecSandbox(command=["opencode", "--prompt", "..."])`
to send a prompt to an OpenCode sandbox. Response is streamed back.

**Hurdle:** No Kagenti backend adapter for ExecSandbox gRPC exists yet. Need to
implement a gRPC client in the FastAPI backend that bridges A2A requests to
ExecSandbox calls.

**Impact on tests:** Would enable 8 `openshell_opencode` and `openshell_claude` tests.

---

## 5. Observability and Audit

### Q5.1: How should supervisor events reach Kagenti's OTel pipeline?

**Status:** OPEN

The supervisor logs to stdout. The gateway receives logs via `PushSandboxLogs` gRPC.
Neither exports to OTLP.

**Candidates:**
1. **Supervisor OTLP exporter:** Upstream contribution — add OTLP exporter to supervisor.
2. **Gateway OTLP exporter:** Gateway aggregates supervisor logs and exports via OTLP.
3. **Sidecar collector:** OTel collector sidecar in sandbox pod scrapes supervisor logs.

### Q5.2: How does Kagenti get LLM usage data from the supervisor proxy?

**Status:** OPEN

The supervisor's HTTP CONNECT proxy handles LLM egress but doesn't instrument
token counts, latency, or cost. Kagenti tracks these via `LlmUsagePanel`.

**Candidates:**
1. **Proxy-level instrumentation:** Upstream — supervisor proxy emits OTLP spans with
   token counts extracted from HTTP response bodies.
2. **Agent-side instrumentation:** Each agent SDK (ADK, Anthropic) emits its own spans.
   Already partially implemented in agent code.
3. **LiteLLM tracking:** LiteLLM records all requests. Kagenti reads from LiteLLM's DB.

### Q5.3: OCSF vs Kagenti event schema for audit trail?

**Status:** OPEN

OpenShell uses OCSF (Open Cybersecurity Schema Framework). Kagenti uses custom
event schema stored in PostgreSQL.

**Candidates:**
1. **OCSF everywhere:** Kagenti adopts OCSF for audit events. More industry-standard.
2. **Kagenti schema everywhere:** OpenShell exports events in Kagenti's format.
3. **Dual export:** Both formats via adapters. Most flexible, most complex.

---

## 6. Multi-Tenancy and Lifecycle

### Q6.1: Should Kagenti operator manage per-tenant gateway lifecycle?

**Status:** OPEN

The proposal recommends gateway-per-tenant (one gateway per team namespace).

**Candidates:**
1. **Operator-managed:** Kagenti operator creates gateway StatefulSet per namespace.
   AgentRuntime CR triggers gateway provisioning.
2. **Helm-per-team:** Each team onboarded via `helm install openshell-team-X`.
3. **Shared gateway:** Single gateway with namespace-scoped RBAC (needs upstream multi-tenancy).

### Q6.2: Sandbox garbage collection and TTL?

**Status:** OPEN

Orphaned PVCs and Sandbox CRs accumulate. No automatic cleanup exists.

**Candidates:**
1. **CronJob cleanup:** Per-namespace CronJob deletes sandboxes older than TTL.
2. **Operator finalizers:** Kagenti operator adds finalizers to Sandbox CRs for cleanup.
3. **Gateway TTL:** Upstream — gateway auto-deletes sandboxes after configurable timeout.

### Q6.3: AgentTask CRD vs AgentRuntime CRD?

**Status:** OPEN

The proposal introduces AgentTask CRD for headless agents. Kagenti has AgentRuntime CRD.

**Recommendation:** Extend AgentRuntime CRD with a `mode: headless` field rather
than creating a separate CRD. Avoids CRD proliferation.

### Q6.4: Supervisor-gateway version consistency?

**Status:** ANSWERED

Current state: version `0.0.0`, all images use `:latest`. Tight coupling exists
via gRPC protobuf — supervisor and gateway must match versions.

**Answer:** Pin to git SHA tags. Init container approach makes this easy — Helm
chart controls both supervisor and gateway image tags.

---

## 7. Testing Hurdles

### Q7.1: How to test OPA egress blocking without `curl`?

**Status:** ANSWERED (fixed)

The supervised agent pod doesn't have `curl` installed. Use `python3 -c "import urllib.request; ..."` instead — python3 is available in all agent images.

**Impact:** Fixes `test_hitl__opa_denies_unauthorized_egress` (was failing).

### Q7.2: How to test openshell_opencode skill execution with LiteMaaS?

**Status:** INVESTIGATING

OpenCode uses OpenAI-compatible API. LiteMaaS provides that endpoint. The gateway
has `OPENAI_API_KEY` + `OPENAI_BASE_URL` env vars set by the fulltest script.

**Hurdles:**
1. Need to verify gateway's provider auto-discovery injects credentials into sandbox pods
2. Need to create a sandbox with OpenCode and send a skill prompt via ExecSandbox or kubectl exec
3. OpenCode may need specific config files (`.opencode/config.yaml`) to use the provider

**Investigation path:**
- Create a sandbox with the base image
- `kubectl exec` into it and check `env | grep OPENAI` to see if credentials are injected
- Try running `opencode --help` to understand CLI flags for non-interactive mode
- If credentials are injected, try `echo "review this code: def f(x): eval(x)" | opencode`

**Impact:** Would enable 3 `openshell_opencode` skill tests.

### Q7.3: How to test context continuity without backend session store?

**Status:** BLOCKED (needs Kagenti backend work)

No agent preserves contextId across requests. The ADK agent creates a new contextId
per request (upstream `to_a2a()` behavior).

**Workaround options:**
1. **Backend session store:** Kagenti backend reconstructs context from DB each turn.
   This is the long-term solution (Phase 2).
2. **Agent-side history in prompt:** Include prior conversation in each A2A request text.
   Quick hack: `a2a_send(url, f"Previous: {history}\n\nNew: {msg}")`.
   Test would verify agent references prior context in response.
3. **ADK session fixture:** Use ADK's built-in session management (not exposed via to_a2a).

**Impact:** Would enable 4 `test_context_continuity` tests.

### Q7.4: How to test PVC data persistence across sandbox restarts?

**Status:** TESTED (partial — gated behind OPENSHELL_DESTRUCTIVE_TESTS)

The `test_resume__generic_sandbox__write_delete_recreate_read` test works but is
gated because it deletes sandbox pods. The `test_workspace_read` test skips if
the gateway doesn't recreate the pod fast enough.

**Hurdles:**
1. Sandbox controller may not recreate pods automatically after CR re-apply
2. Base image pull (1.1GB) takes time on first run
3. PVC binding is `WaitForFirstConsumer` — PVC stays Pending until a pod references it

**Investigation path:**
- Pre-pull base image in fulltest script (already done)
- Increase timeout for pod recreation (from 15s to 60s)
- Verify Sandbox controller reconciles after CR re-apply

**Impact:** Would enable `test_workspace_read__generic` and `test_resume__generic_sandbox`.

### Q7.5: How to test supervised agent connectivity without port-forward?

**Status:** OPEN

The supervisor's network namespace blocks `kubectl port-forward`. A2A tests
require port-forward to reach agents from the test runner.

**Candidates:**
1. **kubectl exec:** Test via `kubectl exec` into the supervised pod, not port-forward.
   Already used for HITL and supervisor enforcement tests.
2. **Test runner pod:** Run pytest from inside the cluster as a Job. Pods can reach
   ClusterIP services directly — no port-forward needed.
3. **Supervisor proxy port:** Upstream — supervisor exposes agent port through the
   OPA proxy (not yet supported).

**Impact:** Would enable `test_agent_card__weather_supervised` and
`test_context_isolation__weather_supervised`.

### Q7.6: How to test Claude Code native skill execution?

**Status:** BLOCKED (needs real Anthropic API key)

Claude Code CLI validates model names against Anthropic's model catalog. It cannot
use LiteMaaS (which provides llama-scout-17b, not a Claude model).

**Candidates:**
1. **Real Anthropic key:** Provide `ANTHROPIC_API_KEY` as a CI secret. Most direct.
2. **Mock API:** Deploy a mock Anthropic API that accepts any model name. Complex.
3. **OpenCode instead:** Test native skill execution with OpenCode (uses OpenAI format,
   works with LiteMaaS). OpenCode can also read `.claude/skills/` if configured.

**Impact:** Would enable 3 `openshell_claude` skill tests. The OpenCode alternative
would enable 3 `openshell_opencode` tests instead — similar validation value.

### Q7.7: How to enable destructive tests in CI safely?

**Status:** OPEN

Destructive tests (scale-down/up) are gated behind `OPENSHELL_DESTRUCTIVE_TESTS=true`
because they kill session-scoped port-forward fixtures.

**Candidates:**
1. **Separate test run:** CI runs non-destructive tests first, then destructive tests
   in a second pytest invocation. Port-forwards are fresh for each run.
2. **Test runner pod:** Run tests from inside the cluster. No port-forward needed.
3. **Port-forward resilience:** Make port-forward fixtures reconnect after agent restart.
   Complex — kubectl port-forward doesn't support reconnection.

**Impact:** Would enable 4 `test_restart` tests and 1 `test_resume` test.

---

## 8. Upstream Dependencies — What Waits on OpenShell Rearchitecture

### Q8.1: Can custom A2A agents use OpenShell credential management today?

**Status:** BLOCKED (needs upstream `--expose-port` or port bridge sidecar)

Custom A2A agents (ADK, Claude SDK) need to be accessible via K8s Service
on port 8080. The supervisor's netns blocks this. Two solutions:

1. **Port bridge sidecar (Kagenti contribution):** socat container bridges
   pod network → netns. Can implement now without upstream changes.
2. **Upstream `--expose-port` flag:** Supervisor natively reverse-proxies
   inbound traffic. Cleaner but needs upstream PR.

**Impact:** Would enable unified credential management for ALL agents.

### Q8.2: Can builtin sandbox agents use LiteMaaS without model validation issues?

**Status:** BLOCKED (needs upstream model-agnostic inference routing)

Both Claude Code and OpenCode validate model names against hardcoded lists.
`llama-scout-17b` is not in either list. The OpenShell inference router
could do model ID rewriting (`gpt-4` → `llama-scout-17b`) but this is
not yet configurable.

**Workaround:** Use the Budget Proxy or LiteLLM with model aliases.
**Upstream fix:** Configurable model ID mapping in the inference router.

### Q8.3: Does the OpenShell RFC 0001 rearchitecture address these blockers?

**Status:** INVESTIGATING

[RFC 0001](https://github.com/NVIDIA/OpenShell/pull/836) introduces:
- Pluggable compute drivers (Kagenti as driver)
- Pluggable credential backends (K8s Secrets, Vault)
- Composable subsystems (compute, credentials, identity, sandbox identity)

This architecture would enable:
- Kagenti-managed credential injection without supervisor netns issues
- Model routing through the credential subsystem
- Custom compute drivers that don't create netns when not needed

**Impact:** Would resolve Q8.1 and Q8.2 cleanly.

### Q8.4: What upstream PRs exist for our blockers?

**Status:** ANSWERED (2026-04-24 research)

| Blocker | Upstream Status | PR | Notes |
|---------|----------------|-----|-------|
| Port exposure for A2A+supervisor | **No PRs** | N/A | PR #867 (merged) removed direct gateway→sandbox; arch is outbound-only |
| Model ID rewriting | **Closed PR** | #618 | Proposed per-request model aliases; closed. Merged approach: static model binding |
| Provider injection for Sandbox CRs | **Implemented** | Production | Works when sandbox created via gateway (has OPENSHELL_SANDBOX_ID) |
| RFC 0001 rearchitecture | **Open** | #836 | Documentation RFC, not feature implementation |

**Key finding:** Provider injection (blocker 3) is already solved upstream.
Our tests create Sandbox CRs via kubectl which bypasses the gateway. Creating
via gateway's `CreateSandbox` gRPC would give us credential injection for free.

**Action items:**
1. Port bridge sidecar: Kagenti contribution (no upstream changes needed)
2. Model rewriting: Use LiteLLM model aliases as workaround
3. Sandbox creation: Use gateway `CreateSandbox` gRPC instead of kubectl
