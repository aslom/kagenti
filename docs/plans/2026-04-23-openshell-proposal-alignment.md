# OpenShell Integration Proposal — Alignment with Kagenti PoC

**Date:** 2026-04-23
**Source:** "OpenShell on Kubernetes/OpenShift: Integration Proposal" (Paolo Dettori, 2026-04-21)
**PoC Branch:** `feat/stream1-sandbox-agent`
**Status:** Working document for PR review discussion

---

## Executive Summary

Paolo's proposal describes a phased plan to run OpenShell directly on production Kubernetes/OpenShift clusters, eliminating the embedded K3s layer. Our PoC (`feat/stream1-sandbox-agent`) has already implemented significant portions of Phase 0 and parts of Phase 1, with some design choices that differ from the proposal. This document maps each section of the proposal against our current implementation, identifies alignment and divergence points, and raises questions for discussion.

**Current PoC status (2026-04-23):**
- Kind: 72 passed, 0 failed, 7 skipped
- HyperShift (OCP): 70 passed, 0 failed, 9 skipped
- All 4 agents deployed: weather-agent, adk-agent, claude-sdk-agent, weather-agent-supervised
- LLM tests running via LiteMaaS on both platforms
- Fulltest script (`openshell-full-test.sh`) is idempotent for both Kind and OCP

---

## Section 2: High-Level Architecture

### 2.1 Key Components

**Proposal says:**
> | Component | Crate | Role |
> | Gateway | openshell-server | Control plane: gRPC/mTLS API, sandbox lifecycle, SSH bridge, provider/credential management, policy store |
> | Supervisor | openshell-sandbox | Runs inside each sandbox pod (as root, then drops privileges). Sets up network namespace, proxy, OPA engine, Landlock/Seccomp, SSH server, then execs the AI agent |
> | K8s Driver | openshell-driver-kubernetes | Creates/watches/deletes Sandbox CRDs via kube-rs |
> | CRD Controller | agent-sandbox-controller | Upstream K8s controller. Reconciles Sandbox CRDs into Pods + PVCs |

**PoC alignment:**
- Gateway: Deployed as StatefulSet in `openshell-system` with `ghcr.io/nvidia/openshell/gateway:latest`. Uses SQLite (`--db-url sqlite://`). **Aligned.**
- Supervisor: Deployed via multi-stage Docker build in `weather-agent-supervised`. Binary copied from `ghcr.io/nvidia/openshell/supervisor:latest`. **Partially aligned** — proposal recommends init container + emptyDir (Section 3.3 Option C), while our PoC bakes it into the image (Option B).
- K8s Driver: Included in the gateway binary. **Aligned.**
- CRD Controller: We install the `agents.x-k8s.io/v1alpha1` Sandbox CRD from upstream. **Aligned.**

### 2.2 Protection Layers

**Proposal says:**
> | Filesystem | Linux Landlock LSM — kernel-level path allowlist | Locked at sandbox creation |
> | Network | HTTP CONNECT proxy (forced via veth/netns) + OPA/Rego rules + TLS MITM | Hot-reloadable at runtime |
> | Process | Seccomp BPF — syscall allowlist | Locked at sandbox creation |
> | Inference | Credential stripping + backend credential injection + model ID rewriting | Hot-reloadable at runtime |

**PoC alignment:**
- Landlock: **Verified in tests.** `test_supervisor_enforcement.py` confirms ABI V3, 14+ rules applied.
- Network namespace: **Verified.** Tests confirm veth pair (10.200.0.1/10.200.0.2), OPA proxy on 10.200.0.1:3128.
- Seccomp: **Partially verified.** Tests check that seccomp is not disabled but don't verify specific BPF filter.
- Inference routing: **Not yet integrated.** Our agents call LiteMaaS directly. The proposal's inference router (credential stripping + injection) is not yet wired up.

**Question 1:** The proposal's inference router would replace our current approach of agents calling LiteMaaS directly. Should we integrate the OpenShell inference router, or keep using LiteMaaS/Budget Proxy as the LLM routing layer?

**Offered alternatives:**
- A) Use OpenShell inference router for all agent LLM calls (proposal's recommended path)
- B) Keep LiteMaaS/Budget Proxy as the routing layer, bypass OpenShell's inference router
- C) Hybrid: Use OpenShell inference router for sandboxed agents, LiteMaaS for custom agents

**Impact on PoC:** Currently no impact. Future architecture needs to decide which LLM routing layer is canonical.

---

## Section 3: Integration Plan

### 3.1 Design Decisions

**Proposal says:**
> | Supervisor delivery | Init container + emptyDir | No node-level access required. Works with OpenShift restricted SCC |
> | Ingress (OpenShift) | Passthrough Route | Native DNS integration |
> | Ingress (Kind/K8s) | Istio Gateway API + TCPRoute | L4 passthrough |
> | Store backend | PostgreSQL | Required for multi-instance gateway and multi-tenancy |

**PoC divergence:**
1. **Supervisor delivery:** We use Option B (bake into image) instead of Option C (init container + emptyDir). The proposal explicitly recommends Option C because it decouples supervisor version from image version.

2. **Ingress:** We don't expose the gateway externally. Our agents are accessed via A2A protocol directly through port-forward or ClusterIP services, not through the gateway's gRPC/mTLS API. This is a fundamental architectural difference.

3. **Store backend:** We use SQLite (`--db-url sqlite://`), not PostgreSQL. For the PoC this is fine; for multi-tenancy/HA, PostgreSQL is needed.

**Question 2:** Should we switch to init container + emptyDir for supervisor delivery to match the proposal?

**Offered alternatives:**
- A) Switch to init container + emptyDir (proposal's recommendation). Decouples supervisor from agent image. More flexible.
- B) Keep bake-into-image (current PoC). Simpler for PoC. Couples supervisor version to image.
- C) Support both: init container for built-in sandboxes, bake for custom agents.

**Impact on PoC:** Moderate. Would require changes to `weather-agent-supervised/Dockerfile` and deployment YAML. Tests should still pass as they verify supervisor behavior, not delivery mechanism.

### 3.3 Supervisor Binary Delivery (detailed)

**Proposal says:**
> **Option C: Init container + emptyDir (recommended)** An init container downloads or copies the supervisor binary into an emptyDir volume shared with the agent container.
> *Pros:* No node-level access. Works with OpenShift restricted SCC. No hostPath needed. Supervisor version decoupled from sandbox image.
> *Cons:* Adds a few seconds of startup latency per sandbox.

**PoC status:** We currently use Option B. Our `weather-agent-supervised/Dockerfile` does a multi-stage build:
```dockerfile
FROM ghcr.io/nvidia/openshell/supervisor:latest AS supervisor
FROM ghcr.io/kagenti/agent-examples/weather_service:latest
COPY --from=supervisor /usr/local/bin/openshell-sandbox /usr/local/bin/openshell-sandbox
```

This works but means every supervisor update requires rebuilding all agent images.

### 3.4 Ingress Strategy

**Proposal says:**
> The CLI communicates with the gateway over gRPC/mTLS, and the SSH tunnel is bridged as raw TCP inside that stream (HTTP CONNECT upgrade). This means the ingress must operate at L4 (TCP passthrough).

**PoC status:** We do NOT use the CLI→gateway→SSH tunnel path. Instead, our agents are standalone A2A services accessed directly. The gateway is deployed but primarily serves as the K8s driver for Sandbox CRD management, not as a CLI bridge.

**Question 3:** Do we plan to support the CLI→gateway→SSH tunnel path for interactive developer sandboxes, or only the A2A agent pattern?

**Offered alternatives:**
- A) Both: A2A for custom agents, CLI+SSH for interactive sandboxes (full proposal scope)
- B) A2A only: Simpler architecture, no SSH tunnel complexity
- C) A2A primary, CLI+SSH as future extension

**Impact on PoC:** If CLI+SSH is needed, we'd need to implement the ingress strategy (Passthrough Route on OCP, Istio TCPRoute on Kind). Currently out of scope.

---

## Section 4: Multi-Tenancy

**Proposal says:**
> **Gateway-per-tenant model (recommended — no code changes):** Instead of waiting for upstream multi-tenant support, deploy one gateway instance per team namespace.

**PoC status:** We deploy a single shared gateway in `openshell-system` that manages sandboxes across namespaces (currently `team1`). This aligns with the proposal's Phase 0 (single-tenant PoC).

**Question 4:** For Phase 1 (multi-tenant OCP), should we adopt the gateway-per-tenant model or wait for upstream multi-tenancy?

**Offered alternatives:**
- A) Gateway-per-tenant (proposal's recommendation). No upstream changes needed. Physical isolation.
- B) Shared gateway. Requires upstream changes for user identity, RBAC, namespace targeting.
- C) Kagenti operator manages gateway lifecycle — creates one per team namespace automatically.

**Impact on PoC:** No immediate impact. Future architecture decision.

---

## Section 5: Gaps and Unsolved Areas

### Key gaps relevant to our PoC:

| Gap | Proposal Severity | PoC Status | Notes |
|-----|-------------------|------------|-------|
| No user identity in gateway | Blocker (shared gw) | N/A | Our PoC uses A2A, bypasses gateway auth entirely |
| No external IdP integration | High | Kagenti has Keycloak | Proposal Phase 2 plans Keycloak integration |
| Gateway HA | High | Not addressed | Single instance with SQLite in PoC |
| No OpenTelemetry integration | Medium | Kagenti has Phoenix/OTel | Our agents have OTel instrumentation already |
| High privilege requirements | High for OCP | `privileged: true` | Proposal suggests SYS_ADMIN + NET_ADMIN + SYS_PTRACE without full `privileged` |
| No external secret management | Medium | LiteMaaS via K8s Secrets | Proposal wants Vault/Keycloak integration |

**Question 5:** The proposal identifies SCC requirements as the "single biggest friction point with cluster administrators." Our PoC uses `privileged: true` for the supervised agent. Should we narrow to specific capabilities?

**Proposal says:**
> allowPrivilegedContainer: false
> requiredDropCapabilities: ALL
> allowedCapabilities: NET_ADMIN (veth + netns), SYS_ADMIN (Landlock LSM), SYS_PTRACE (Seccomp BPF)
> runAsUser: RunAsAny

**PoC status:** We use `privileged: true` because `mount --make-shared` in the supervisor requires it on Kind. The proposal's SCC is more restrictive.

**Offered alternatives:**
- A) Switch to specific capabilities (NET_ADMIN, SYS_ADMIN, SYS_PTRACE) per the proposal
- B) Keep `privileged: true` for PoC, narrow in production
- C) Use init container approach (Option C) which may reduce privilege requirements

**Impact on PoC:** Significant. Needs testing on both Kind and OCP to verify supervisor works with reduced privileges.

---

## Section 6: Phased Roadmap

### Phase 0: Single-Tenant PoC on Kind

**Proposal says:**
> 1. Create a Kind cluster with Istio installed
> 2. Deploy the AgentSandbox CRD + controller
> 3. Deploy the gateway as a StatefulSet with SQLite
> 4. Deploy the supervisor using init container + emptyDir
> 5. Expose the gateway via Istio Gateway API (TCPRoute passthrough)
> 6. Configure the CLI to point at the gateway
> 7. Run `openshell sandbox create -- claude` and validate

**PoC status:** Steps 1-3 are **complete**. Step 4 uses Option B instead of Option C. Steps 5-7 are **not implemented** — we use A2A agents instead of the CLI+SSH path.

**PoC goes beyond Phase 0 by:**
- Deploying 4 agents (weather, ADK, Claude SDK, weather-supervised) instead of just Claude CLI
- Running 72 E2E tests covering platform health, A2A conversations, LLM skills, credential isolation, sandbox lifecycle, supervisor enforcement
- Supporting both Kind and HyperShift from a single idempotent fulltest script
- Integrating LiteMaaS for LLM tests on both platforms

### Phase 1: Multi-Tenant on OpenShift

**Proposal says:**
> Each team namespace contains a complete, isolated OpenShell stack. Teams are onboarded via a Helm release parameterized by team name.

**PoC status:** We deploy on HyperShift (OCP) with a single shared gateway. The Kagenti platform (operator, Keycloak, SPIRE, Istio) is installed via `scripts/ocp/setup-kagenti.sh`. Multi-tenancy is not yet implemented.

### Phase 2: Kagenti Integration

**Proposal says:**
> | Provider credentials | Keycloak client credentials | Store provider secrets in Keycloak; gateway fetches via OIDC token exchange |
> | mTLS | SPIRE workload identity (SPIFFE SVIDs) | Replace OpenShell's cert management with SPIRE-issued SVIDs |
> | OPA/Rego policy | Kagenti policy framework | Unify policy definition |
> | Sandbox pod injection | kagenti-webhook | Extend webhook to inject sidecars |
> | Inference routing | Kagenti backend routing | Route inference through kagenti's infrastructure |

**PoC status:** This is the most significant area of future work. None of these integration points are implemented in the PoC. The PoC demonstrates that OpenShell and Kagenti can coexist on the same cluster and that OpenShell's security layers work, but the deep integration described in Phase 2 is not yet started.

**Question 6:** Which Phase 2 integration point should we prioritize?

**Offered alternatives:**
- A) Provider credentials via Keycloak (most impactful for multi-user scenarios)
- B) SPIRE SVID integration (replaces OpenShell's own mTLS, enables zero-trust)
- C) Inference routing through Kagenti backend (centralizes LLM access)
- D) Webhook sidecar injection (most natural for existing Kagenti architecture)

---

## Section 7: Upstream Contributions

**Proposal's priority list for upstream OpenShell PRs:**

| Priority | Contribution | PoC Impact |
|----------|-------------|------------|
| Critical | User identity and RBAC | Needed for shared gateway multi-tenancy |
| Critical | OIDC / external IdP | Kagenti has Keycloak — this enables integration |
| Critical | Configurable sandbox namespace | Currently hardcoded in our PoC |
| High | Init container supervisor delivery | We use Option B; this enables Option C |
| High | Service-per-sandbox SSH routing | Not relevant for A2A pattern |
| High | Gateway HA | Production requirement |
| High | Health/readiness endpoints | We work around this with TCP probes |
| Medium | Policy hierarchy | Important for multi-team governance |
| Medium | OpenTelemetry integration | Kagenti already has OTel; this bridges the gap |

---

## Section 8: Open Questions

1. **Sandbox pod SCC on OpenShift:** Our PoC uses `privileged: true`. The proposal asks about minimum privilege set. **Needs testing.**

2. **Gateway HA:** Not addressed in PoC. "Acceptable for PoC/dev, not for production workloads."

3. **Image pull policy:** On air-gapped clusters, images need mirroring. Our PoC uses OCP internal registry binary builds — this partially addresses the concern.

4. **Supervisor binary versioning:** With init container approach, which version to pin? Our PoC uses `latest` tag.

5. **Persistent sandboxes on OpenShift:** Not tested in PoC.

---

## Session Persistence & Headless Mode

**Proposal says:**
> Phase 1: dtach via Init Container (No Upstream Changes)
> Deliver dtach — a minimal (~20KB) single-binary process detacher — via the same init container that delivers the supervisor binary.

**PoC status:** Not implemented. Our agents are stateless A2A services. Session persistence would be needed for interactive Claude Code sandboxes.

**Proposal also introduces AgentTask CRD:**
> Build a standalone Kubernetes controller outside OpenShell. The AgentTask CRD defines headless workloads with: image, entrypoint, workspace PVC, timeout, OPA policy, resource limits, and output configuration.

**Question 7:** Should we implement the AgentTask CRD as part of the Kagenti operator, or as a separate controller?

---

## Gap Analysis: UI, Session Exposure, and Agent Visibility

### What the proposal says (or doesn't say)

The proposal is **entirely CLI-focused**. There is no mention of a web UI, dashboard, web-based session management, or any browser-accessible interface for managing sandboxes or viewing agent sessions. The only user interaction model described is:

1. **CLI → Gateway → SSH tunnel → Agent terminal** — the user runs `openshell sandbox create -- claude`, gets an SSH session into the sandbox, interacts with the agent in a terminal.
2. **`openshell term`** — shows sandbox status in the terminal.
3. **Session listing** — the proposal mentions `openshell sandbox sessions <sandbox-id>` as a future gateway feature, but this is not yet implemented.

Specific gaps identified:

| Capability | Proposal Status | Notes |
|-----------|----------------|-------|
| Web UI for sandbox management | Not mentioned | No web interface at all |
| Session listing/browsing | CLI-only, future | `openshell sandbox sessions` planned |
| Agent catalog/marketplace | Not mentioned | No concept of browsing available agents |
| Session visualization/graphs | Not mentioned | No graph views, topology, or step tracking |
| LLM usage tracking | Not mentioned | No token counts, cost tracking, or usage dashboards |
| Pod status monitoring | Not mentioned | Users rely on `kubectl` |
| Prompt inspection | Not mentioned | No visibility into agent-LLM interactions |
| Human-in-the-loop approval | Not mentioned | No HITL mechanism |
| Build progress tracking | Not mentioned | No build pipeline visibility |
| Observability integration | Gap identified (Section 5.6) | "No OpenTelemetry integration. No LLM observability." |
| Audit trail | Gap identified (Section 5.6) | "No centralized audit log. Cannot answer 'who did what, when'" |
| Multi-session management | Not mentioned | No sub-session support |

The proposal's observability section (5.6) explicitly acknowledges these gaps:

> **No OpenTelemetry integration** — No traces, no metrics export. Cannot correlate sandbox operations with cluster-level observability.
>
> **No LLM observability** — Agent-to-LLM API calls pass through the proxy but are not instrumented. No token counts, latency, or cost tracking.
>
> **No centralized audit log** — User actions are not emitted as structured audit events.

### What Kagenti already provides

Kagenti has a comprehensive web UI (`kagenti/ui-v2/`) with the following capabilities that directly address the proposal's gaps:

**Sandbox Management UI:**
- `SandboxPage` — Main sandbox management interface
- `SandboxesPage` — List/overview of all sandboxes
- `SandboxCreatePage` — Wizard-based sandbox creation
- `SandboxWizard` — Step-by-step guided sandbox configuration
- `SandboxConfig` — Detailed sandbox configuration panel
- `SandboxAgentsPanel` — Panel showing agents running in a sandbox

**Session Management UI:**
- `SessionSidebar` — Navigation sidebar for browsing sessions
- `SessionStatsPanel` — Real-time statistics for active sessions
- `SubSessionsPanel` — Nested/child session management
- `SessionGraphPage` — Full-page session interaction graph
- `SessionsTablePage` — Tabular view of all sessions with filtering

**Agent Interaction UI:**
- `AgentChat` — Interactive chat interface with agents (A2A protocol)
- `AgentCatalogPage` — Browse and discover available agents
- `AgentDetailPage` — Detailed view of agent configuration and status
- `AgentLoopCard` — Visualization of agent reasoning loops
- `DelegationCard` — Agent task delegation visualization
- `HitlApprovalCard` — Human-in-the-loop approval interface
- `ModelSwitcher` — Switch between LLM models
- `ModelBadge` — Display current model information

**Visualization Components:**
- `TopologyGraphView` — System topology DAG visualization
- `StepGraphView` — Per-step agent execution graph
- `GraphLoopView` — Agent loop visualization
- `GraphDetailPanel` — Detailed graph element inspector
- `EventsPanel` — Real-time event stream viewer
- `LoopDetail` — Detailed loop breakdown
- `LoopSummaryBar` — Summary statistics for loops
- `FloatingViewBar` — View mode switcher

**Observability Components:**
- `LlmUsagePanel` — LLM token usage, cost, and latency tracking
- `PodStatusPanel` — Kubernetes pod status monitoring
- `PromptInspector` — Full prompt/response inspection
- `EventSubtypeGraphView` — Event categorization visualization

**Backend API (FastAPI):**
- Agent CRUD operations
- Session creation, listing, streaming
- A2A protocol endpoints
- Build/deployment progress tracking
- LLM usage tracking endpoints
- Pod status retrieval

### Alignment Assessment

The proposal operates at the **infrastructure layer** (Kubernetes deployment, security, networking) while Kagenti operates at the **application layer** (UI, session management, agent interaction, observability). They are complementary, not competing:

| Layer | OpenShell Proposal | Kagenti |
|-------|-------------------|---------|
| **Security** | Landlock, seccomp, netns, OPA | AuthBridge, SPIRE, Keycloak |
| **Sandbox lifecycle** | Sandbox CRD, gateway, supervisor | Operator, AgentRuntime CRD |
| **Agent interaction** | CLI + SSH terminal | Web UI + A2A protocol |
| **Session management** | In-memory SandboxIndex | PostgreSQL sessions DB, SSE streaming |
| **Observability** | None (gap) | Phoenix, OTel, MLflow, LlmUsagePanel |
| **User identity** | mTLS certificates | Keycloak OIDC |
| **LLM routing** | Inference router (planned) | LiteLLM, Budget Proxy |
| **Build pipeline** | Not mentioned | Shipwright, OCP BuildConfig |

**Key insight:** The proposal's Phase 2 (Kagenti Integration) is where these two systems meet. OpenShell provides the sandbox security runtime, while Kagenti provides the management plane, UI, and observability. The proposal acknowledges this by listing integration points (Keycloak, SPIRE, OPA unification, webhook injection, inference routing) but does not describe how the user experience should work.

**Question 8:** How should the Kagenti UI expose OpenShell sandbox sessions?

**Offered alternatives:**
- A) **Embed terminal in UI:** The Kagenti web UI embeds an xterm.js terminal that connects to the OpenShell gateway via WebSocket, bridged to the SSH tunnel. Users can create, list, and connect to sandboxes from the browser.
- B) **Status-only in UI, CLI for interaction:** The Kagenti UI shows sandbox status (running, idle, resources used) but interactive access remains CLI-only via `openshell sandbox connect`.
- C) **Hybrid:** The Kagenti UI provides full sandbox lifecycle management (create, configure, destroy) via the existing SandboxWizard, while agent interaction uses the A2A chat interface for custom agents and CLI+SSH for interactive sandboxes.
- D) **A2A-first:** All agent interaction goes through the A2A protocol and the AgentChat UI. The CLI+SSH path is an escape hatch for debugging, not the primary user experience.

**Recommendation:** Option D (A2A-first) for custom agents, with Option C as the long-term vision. The A2A protocol is already implemented and tested in our PoC. The CLI+SSH path adds significant complexity (ingress, mTLS, SSH bridging) and should only be added when interactive Claude Code sandboxes are a concrete requirement.

---

## Summary of Alignment

| Proposal Area | PoC Status | Alignment |
|---------------|-----------|-----------|
| Gateway deployment | Deployed | Aligned |
| Supervisor delivery | Baked into image | Diverged (proposal recommends init container) |
| Sandbox CRD | Installed | Aligned |
| Agent model | A2A custom agents | Extended beyond proposal (proposal focuses on CLI) |
| Supervised agent | weather-agent-supervised | Aligned (Landlock, seccomp, netns, OPA verified) |
| LLM integration | LiteMaaS direct | Diverged (proposal uses inference router) |
| Kind deployment | Idempotent fulltest | Exceeds proposal Phase 0 |
| OCP deployment | HyperShift with OCP binary builds | Exceeds proposal Phase 0, partially Phase 1 |
| E2E tests | 72 Kind / 70 OCP | Exceeds proposal validation criteria |
| Multi-tenancy | Single gateway, single namespace | Aligned with Phase 0 |
| Keycloak integration | Not yet | Phase 2 scope |
| SPIRE integration | Not yet | Phase 2 scope |
| CLI + SSH tunnel | Not implemented | Out of current scope |

---

## E2E Test Coverage Comparison

### Proposal's Phase 0 validation criteria vs our E2E tests

The proposal defines 4 validation criteria for Phase 0 (Single-Tenant PoC on Kind). Our PoC has 72 passing tests on Kind and 70 on HyperShift. Here is how they map:

| Proposal Phase 0 Criterion | Our Tests | Status |
|---|---|---|
| "Sandbox creates, connects, and destroys cleanly" | `test_create_sandbox`, `test_delete_sandbox`, `test_gateway_processes_sandbox`, `test_list_sandboxes` | **Covered** |
| "`openshell term` shows sandbox status" | Not tested | **Not covered** — we don't use the CLI. Our UI provides equivalent via `SandboxPage` |
| "Network policy (OPA) blocks disallowed egress" | `test_opa_policy_loaded`, `test_policy_has_network_rules`, `test_rego_file_mounted`, `test_tls_termination_enabled` | **Covered** — policy loaded and configured. Live enforcement blocking is validated indirectly: agents with netns can only reach LLM endpoints defined in policy |
| "Sandbox survives CLI disconnect and reconnect (if `--keep` is used)" | Not tested | **Not covered** — we don't use CLI+SSH. A2A agents are long-running services, not ephemeral sessions |

### Proposal's Phase 1 validation criteria vs our E2E tests

| Proposal Phase 1 Criterion | Our Tests | Status |
|---|---|---|
| "All Phase 0 criteria on OpenShift" | 70 tests pass on HyperShift (OCP) | **Covered** (except CLI-specific criteria) |
| "Two or more teams running independently with isolated gateways" | Not tested | **Not covered** — single gateway, single namespace in PoC |
| "Each team's Route works with DNS and TLS passthrough" | Not tested | **Not covered** — no ingress route for gateway |
| "SSH sessions survive idle periods (timeout annotation)" | Not tested | **Not covered** — no SSH sessions |
| "Resource quotas limit sandbox proliferation per team" | Not tested | **Not covered** — no quotas configured |
| "Cross-team isolation confirmed" | Not tested | **Not covered** — single tenant |
| "SELinux enforcing mode does not block sandbox operations" | Not tested directly | **Partially covered** — HyperShift tests pass on OCP nodes with SELinux |
| "Team onboarding is a single Helm command" | Not tested | **Not covered** — we use fulltest script, not Helm-per-team |

### What our PoC tests that the proposal does NOT define as validation criteria

Our PoC's 72 tests on Kind cover significantly more ground than the proposal's 4 Phase 0 criteria:

| Test Category | Test Count | Tests | Proposal Coverage |
|---|---|---|---|
| **Platform health** | 7 | Gateway pods running/ready, operator pod, agent pods exist/running/ready, no crashloops | Not mentioned |
| **A2A agent conversations** | 6 | Weather query (London, multi-city), ADK hello + PR review, Claude SDK hello + code review | Not mentioned — proposal only validates CLI sessions |
| **Credential isolation** | 18 | `secretKeyRef` delivery (2), no hardcoded keys (4), no K8s SA token leak (4), policy YAML mounted (4), policy valid (4), supervisor PID 1 check (1), placeholder tokens (2) | Not mentioned — proposal acknowledges credential gap in Section 5.5 but defines no tests |
| **Skill discovery** | 5 | Skills ConfigMap creation, index validation, review skill exists, weather agent lists skills, Claude SDK has code_review skill | Not mentioned — proposal has no concept of skills testing |
| **Skill execution** | 6 | Claude SDK PR review/RCA/security review with real LLM, ADK PR review with real LLM, skill files exist, compatibility matrix documented | Not mentioned — no LLM-backed testing in proposal |
| **Supervisor enforcement** | 12 | Landlock applied/ABI version/read-only/read-write paths, netns created/OPA proxy/netns name, seccomp not disabled, OPA policy loaded/network rules/rego mounted/TLS MITM, real GitHub PR review, RCA log analysis | Partially — proposal says "validate OPA policy" but defines no specific enforcement tests |
| **Builtin sandboxes** | 5 | Sandbox CR create, gateway sees sandbox, base image CLI check, Claude sandbox responds, OpenCode sandbox responds | Not mentioned — proposal validates only via CLI |
| **Sandbox lifecycle** | 4 | List, create, delete, gateway processes | Partially — proposal says "creates, connects, destroys cleanly" |
| **Multi-framework agents** | 4 agents | Weather (no LLM), Google ADK (LiteLLM), Claude SDK (OpenAI-compat), weather-supervised (OpenShell supervisor) | Not mentioned — proposal only validates Claude CLI |

### Tests the proposal implies but we intentionally skip

| Implied Test | Why Skipped | Alternative in PoC |
|---|---|---|
| CLI → Gateway → SSH connection | A2A-first architecture — no CLI+SSH | A2A `message/send` JSON-RPC tests |
| `openshell term` status display | No CLI used | Kagenti UI `SandboxPage` + `PodStatusPanel` |
| Session reconnect after disconnect | A2A agents are persistent services | Agent pods survive across test runs |
| Multi-tenant namespace isolation | Single-tenant Phase 0 scope | Future work for Phase 1 |

### Full E2E Test Matrix (Kind — 84 tests, HyperShift — similar)

To view the live test matrix, run:
```bash
uv run pytest kagenti/tests/e2e/openshell/ -v --timeout=300 2>&1 | grep -E "PASSED|FAILED|SKIPPED"
```

#### Platform Health (7 tests)

| Test | weather | adk | claude-sdk | supervised | Result |
|------|---------|-----|------------|------------|--------|
| gateway_pod_running | - | - | - | - | PASS |
| gateway_containers_ready | - | - | - | - | PASS |
| operator_pod_running | - | - | - | - | PASS (Kind) / SKIP (HyperShift) |
| all_agent_pods_exist | ALL | ALL | ALL | ALL | PASS |
| all_agent_pods_running | ALL | ALL | ALL | ALL | PASS |
| agent_deployments_ready | ALL | ALL | ALL | ALL | PASS |
| no_crashlooping_agent_pods | ALL | ALL | ALL | ALL | PASS |

#### Credential Isolation (18 tests, parametrized across 4 agents)

| Test | weather | adk | claude-sdk | supervised | Result |
|------|---------|-----|------------|------------|--------|
| api_key_from_secret_ref | - | PASS | PASS | - | PASS |
| no_literal_api_keys | PASS | PASS | PASS | PASS | PASS |
| no_kubernetes_token_exposed | PASS | PASS | PASS | PASS | PASS |
| policy_file_exists | PASS | PASS | PASS | PASS | PASS |
| policy_is_valid_yaml | PASS | PASS | PASS | PASS | PASS |
| supervisor_entrypoint | SKIP | SKIP | SKIP | PASS | SKIP (3) + PASS (1) |
| placeholder_tokens | - | SKIP | SKIP | - | SKIP (2) |

#### A2A Agent Conversations (6 tests, LLM-gated)

| Test | weather | adk | claude-sdk | Result |
|------|---------|-----|------------|--------|
| hello | - | PASS | PASS | PASS |
| weather_query_london | PASS | - | - | PASS |
| weather_query_multi_city | PASS | - | - | PASS |
| pr_review | - | PASS | - | PASS |
| code_review | - | - | PASS | PASS |

#### Multi-Turn Conversations (6 tests, LLM-gated, parametrized)

| Test | adk | claude-sdk | Result |
|------|-----|------------|--------|
| responds_to_sequential_messages | PASS | PASS | PASS |
| context_isolation | PASS | PASS | PASS |
| context_continuity | SKIP | SKIP | SKIP (needs upstream or PVC store) |

#### Scale-Down/Up Persistence (4 tests, parametrized)

| Test | weather | adk | claude-sdk | Result |
|------|---------|-----|------------|--------|
| survives_scale_cycle | PASS | PASS | PASS | PASS |
| pod_uid_changes_after_scale | PASS | - | - | PASS |

#### PVC Workspace Persistence (4 tests, parametrized across sandbox types)

| Test | generic | claude | opencode | Result |
|------|---------|--------|----------|--------|
| writes_session_to_pvc | PASS | PASS | PASS | PASS |
| pvc_survives_sandbox_deletion | ALL | ALL | ALL | PASS |

#### Sandbox Status Observability (5 tests)

| Test | Result |
|------|--------|
| gateway_status_queryable | PASS |
| agent_deployments_status_queryable | PASS |
| agent_pods_status_queryable | PASS |
| sandbox_cr_status_queryable | PASS |
| gateway_logs_accessible | PASS |

#### Agent Service Persistence (3 tests)

| Test | Result |
|------|--------|
| responds_across_connections | PASS |
| stable_after_delay | PASS |
| pod_not_restarted_during_requests | PASS |

#### Supervisor Enforcement (12 tests, requires weather-agent-supervised)

| Test | Result |
|------|--------|
| landlock_applied_in_logs | PASS |
| landlock_abi_version | PASS |
| read_only_paths_configured | PASS |
| read_write_paths_configured | PASS |
| netns_created_in_logs | PASS |
| opa_proxy_listening | PASS |
| netns_name_in_logs | PASS |
| seccomp_not_explicitly_disabled | PASS |
| opa_policy_loaded | PASS |
| policy_has_network_rules | PASS |
| rego_file_mounted | PASS |
| tls_termination_enabled | PASS |

#### Skill Discovery & Execution (11 tests, LLM-gated)

| Test | Result |
|------|--------|
| create_skills_configmap | PASS |
| skills_configmap_has_index | PASS |
| skills_include_review | PASS |
| weather_agent_lists_skills | PASS |
| claude_sdk_has_code_review_skill | PASS |
| claude_sdk_pr_review_skill (real LLM) | PASS |
| adk_pr_review_skill (real LLM) | PASS |
| claude_sdk_rca_skill (real LLM) | PASS |
| claude_sdk_security_review_skill (real LLM) | PASS |
| review_real_github_pr (real LLM + GitHub API) | PASS |
| rca_style_log_analysis (real LLM) | PASS |

#### Builtin Sandboxes (5 tests)

| Test | Result |
|------|--------|
| create_sandbox_cr | PASS |
| gateway_sees_sandbox | PASS |
| base_image_cli_check | PASS (Kind) / SKIP (HyperShift) |
| claude_sandbox_responds | PASS |
| opencode_sandbox_responds | PASS |

#### Sandbox Lifecycle (4 tests)

| Test | Result |
|------|--------|
| list_sandboxes | PASS |
| create_sandbox | PASS |
| delete_sandbox | PASS |
| gateway_processes_sandbox | PASS |

### Key takeaway

The proposal's validation criteria are **infrastructure-focused** (can OpenShell run on K8s?) while our PoC's test suite is **platform-focused** (can agents run securely, communicate via A2A, execute LLM skills, handle credentials properly?). Our PoC exceeds the proposal's Phase 0 scope significantly and partially covers Phase 1 (OCP deployment).

The test matrix shows clear parametrized coverage across agent types (weather, ADK, Claude SDK, weather-supervised) and sandbox types (generic, Claude, OpenCode). Skipped tests document concrete TODOs with explanations rather than missing functionality.

---

## Compiled Questions for Proposal Discussion

The following questions cover areas where the proposal is unclear, incomplete, or where our PoC has discovered issues not addressed by the proposal. These are organized by topic for structured PR review discussion.

### A. Agent Model and User Experience

**Q-A1:** The proposal exclusively describes the CLI+SSH interaction model (`openshell sandbox create -- claude`). How does the proposal envision agents that are long-running services accessible via A2A protocol rather than interactive terminal sessions? Our PoC deploys 4 agents as A2A services that respond to JSON-RPC requests — this model is not mentioned in the proposal.

**Q-A2:** The proposal has no concept of a web UI for sandbox/agent management. Kagenti provides a comprehensive web UI with sandbox wizards, session graphs, agent catalogs, and observability panels. How does the proposal envision the user experience for non-CLI users (e.g., platform operators, team leads reviewing agent activity)?

**Q-A3:** The proposal mentions `openshell sandbox sessions <sandbox-id>` as a future feature for session listing. How does this interact with Kagenti's session management (PostgreSQL sessions DB, SSE streaming, session history)? Should there be a single session management layer, or do OpenShell and Kagenti maintain separate session concepts?

**Q-A4:** The proposal's agent model is single-agent-per-sandbox (one Claude/Codex instance per sandbox). Our PoC deploys multiple agents per namespace, each as a separate Deployment+Service. How does the proposal envision multi-agent orchestration within a team namespace?

### B. Security and Privileges

**Q-B1:** The proposal states that the supervisor requires `NET_ADMIN + SYS_ADMIN + runAsUser: 0` and asks "what is the minimum privilege set?" Our research shows the supervisor's seccomp filter blocks all `mount` syscalls, yet our PoC required `privileged: true` on Kind due to `mount --make-shared`. Is this a Kind-specific issue, or does the supervisor actually call mount operations not visible in the seccomp filter code?

**Q-B2:** The proposal recommends a custom SCC with `allowPrivilegedContainer: false` and specific capabilities. Our PoC uses `privileged: true` on OCP. Has the proposed SCC been tested on an actual OCP cluster? Does SELinux enforcing mode cause additional denials beyond what the capabilities grant?

**Q-B3:** The proposal identifies "no external secret management" as a gap (Section 5.5). Our PoC delivers API keys via K8s `secretKeyRef`, which the credential isolation tests verify. The proposal's credential isolation model uses placeholder tokens (`openshell:resolve:env:*`) resolved by the supervisor proxy. How should these two approaches be reconciled? Should agents use K8s Secrets (Kagenti model) or placeholder tokens (OpenShell model)?

**Q-B4:** The proposal does not mention network egress policy enforcement testing. Our PoC verifies that OPA policies are loaded and network rules are configured, but does not test live egress blocking (e.g., agent tries to reach a blocked endpoint and gets denied). How should live enforcement be validated end-to-end?

### C. LLM Integration and Inference Routing

**Q-C1:** The proposal describes an "inference router" that strips agent credentials and injects backend credentials, with model ID rewriting. Kagenti has LiteLLM and Budget Proxy for similar purposes. Which component should be the canonical LLM routing layer? Should the OpenShell inference router wrap LiteLLM, or should LiteLLM replace the inference router?

**Q-C2:** The proposal's inference routing is hot-reloadable at runtime. LiteLLM's virtual keys support per-key budget limits. How should per-session budget enforcement work when combining both systems? The proposal does not mention budget/cost tracking at all.

**Q-C3:** The proposal lists 11 hardcoded provider plugins in the ProviderRegistry (Anthropic, OpenAI, NVIDIA, GitHub, etc.). Our PoC uses LiteMaaS (llama-scout-17b via LiteLLM) as a unified endpoint. The proposal acknowledges "no custom provider plugins" as a gap. Should the Kagenti LiteLLM instance serve as the pluggable provider backend that the proposal identifies as needed?

### D. Observability and Audit

**Q-D1:** The proposal identifies "no OpenTelemetry integration" as a medium-severity gap. Kagenti already has OTel instrumentation with Phoenix for trace collection. How should OpenShell's supervisor-level events (Landlock applied, netns created, OPA decisions, proxy access logs) be exported to Kagenti's OTel pipeline? Should the supervisor emit OTLP spans, or should a sidecar collector scrape supervisor logs?

**Q-D2:** The proposal identifies "no LLM observability" as a gap — "agent-to-LLM API calls pass through the proxy but are not instrumented." Kagenti tracks LLM usage via `LlmUsagePanel` in the UI. If the OpenShell proxy handles LLM egress, how does Kagenti's observability layer get token counts, latency, and cost data from the proxy?

**Q-D3:** The proposal mentions OCSF (Open Cybersecurity Schema Framework) event format for audit but says "integration points are minimal." Kagenti uses structured events stored in PostgreSQL. Should the audit trail use OCSF format (OpenShell's choice) or Kagenti's event schema? Can both be supported?

### E. Architecture and Lifecycle

**Q-E1:** The proposal recommends gateway-per-tenant for multi-tenancy (Section 4). This means each team namespace gets its own gateway StatefulSet + PostgreSQL + Service + Route. The Kagenti operator currently manages agent deployments per namespace. Should the Kagenti operator also manage per-tenant gateway lifecycle (deploy, scale, monitor gateway instances)?

**Q-E2:** The proposal identifies "no sandbox TTL" and "orphaned resource cleanup" as gaps. Our PoC does not implement TTL or cleanup. Should the Kagenti operator implement sandbox garbage collection (e.g., delete Sandbox CRDs and PVCs older than a configurable TTL)?

**Q-E3:** The proposal introduces an AgentTask CRD for headless/long-running agents (Section: Headless Mode). Kagenti already has an AgentRuntime CRD. Should these be merged into a single CRD, or do they serve different enough purposes to coexist?

**Q-E4:** The proposal states "supervisor binary versioning" is unresolved — version is `0.0.0`, no compatibility matrix, no version negotiation. For production deployments, how should the Kagenti operator ensure supervisor-gateway version consistency across all team namespaces?

### F. Testing and Validation

**Q-F1:** The proposal defines 4 validation criteria for Phase 0, all CLI-focused. Our PoC has 72 passing tests covering 9 categories (platform health, A2A conversations, credential isolation, skill discovery/execution, supervisor enforcement, sandbox lifecycle, builtin sandboxes). Should the proposal adopt a more comprehensive validation framework? Can our test suite serve as the reference E2E suite for the OpenShell-on-K8s effort?

**Q-F2:** The proposal does not mention E2E testing of multi-framework agents. Our PoC tests 4 different agent types (weather/no-LLM, Google ADK, Claude SDK/OpenAI-compat, weather-supervised/OpenShell supervisor). Should the proposal include multi-framework validation as a Phase 0 criterion?

**Q-F3:** The proposal does not mention credential isolation testing. Our PoC has 18 credential isolation tests verifying secretKeyRef delivery, no hardcoded keys, no K8s SA token exposure, and policy ConfigMap mounting. Should credential isolation be a mandatory validation criterion?

**Q-F4:** The proposal's Phase 1 criteria include "SELinux enforcing mode does not block sandbox operations." Our PoC runs on HyperShift (OCP with SELinux enforcing) and passes 70/79 tests. Has this criterion already been met by our PoC, or are there specific SELinux policy modules that need testing?

---

## Answers to the Proposal's Open Questions

The proposal (Section 8) raises 5 open questions plus 2 additional topics (session persistence, headless mode). Based on our PoC implementation, codebase research, and brainstorming, here are our answers.

### Question 1: Sandbox pod SCC on OpenShift

**Proposal asks:**
> The supervisor requires NET_ADMIN + SYS_ADMIN + runAsUser: 0. What is the minimum privilege set that satisfies Landlock, Seccomp BPF, and network namespace creation? Can any of these be dropped with upstream changes?

**Answer from PoC research:**

The supervisor's Seccomp filter explicitly blocks all `mount`-related syscalls, which means the supervisor itself does NOT call `mount`. Our PoC's need for `privileged: true` was caused by Kind-specific behavior, not the supervisor's requirements.

The minimum capability set is:

| Capability | Required for | Can it be dropped? |
|------------|-------------|-------------------|
| `CAP_NET_ADMIN` | Creating veth pairs, network namespaces, assigning IPs, configuring routes | No — fundamental to netns isolation |
| `CAP_SYS_ADMIN` | Creating network namespaces via `unshare()`, `setns()` operations, Landlock ABI access | No — kernel requirement for namespace operations |
| `CAP_SYS_PTRACE` | OPA proxy reading `/proc/<pid>/fd/` and `/proc/<pid>/exe` for processes running as different user | Possibly — only needed for proxy's process inspection |

The recommended OCP SCC:
```yaml
allowPrivilegedContainer: false
requiredDropCapabilities: [ALL]
allowedCapabilities: [NET_ADMIN, SYS_ADMIN, SYS_PTRACE]
runAsUser:
  type: RunAsAny    # supervisor starts as root, drops to sandbox UID
volumes: [emptyDir, persistentVolumeClaim, secret, configMap]
```

**PoC validation needed:** We should test `weather-agent-supervised` with this reduced privilege set on both Kind and HyperShift. If it works, we can remove `privileged: true` from the deployment YAML.

**Future upstream path:** User namespaces (`CAP_SYS_ADMIN` → `CAP_SYS_RESOURCE`) could eliminate the need for `SYS_ADMIN`, but this requires kernel 6.1+ and is not yet supported in OpenShell.

### Question 2: Gateway HA

**Proposal asks:**
> The gateway uses an in-memory SandboxIndex. Running multiple gateway replicas requires either shared state (Redis/Postgres) or leader election. What's the upstream plan for horizontal scaling?

**Answer from codebase research:**

The gateway already supports PostgreSQL as a store backend. The `--db-url` flag accepts both `sqlite://` and `postgres://...` connection strings. The Helm chart at `deploy/helm/openshell/values.yaml` includes a `replicaCount` field.

However, there is **no explicit leader election mechanism**. Multiple replicas would operate independently against the same PostgreSQL database, which could cause:
- Duplicate sandbox watch events
- Race conditions in sandbox creation/deletion
- SSH session routing conflicts (sessions are pinned to a specific gateway pod)

**Our assessment:**
- **Phase 0/1 (PoC):** Single replica with SQLite is sufficient. No HA needed.
- **Phase 2 (Production):** Switch to PostgreSQL. Single replica with PostgreSQL is a reasonable intermediate step — provides persistence without HA complexity.
- **Phase 3 (Scale):** Multiple replicas require upstream work: leader election for watch loops, shared session state, consistent sandbox routing.

**What Kagenti can contribute:**
- The Kagenti operator could manage gateway lifecycle (deploy, scale, failover)
- Istio ambient mesh already provides mTLS between gateway and sandbox pods
- SPIRE could replace OpenShell's self-managed certificates

### Question 3: Image pull policy

**Proposal asks:**
> On air-gapped clusters, images need to be mirrored. Should the proposal include an image mirror strategy?

**Answer from our PoC:**

Our PoC already demonstrates a practical image strategy for OCP clusters:

1. **OCP Binary Builds:** The `openshell-build-agents.sh` script uses `oc new-build --binary` + `oc start-build --from-dir` to build agent images directly on the cluster. Images are pushed to the OCP internal registry (`image-registry.openshift-image-registry.svc:5000/team1/<agent>:latest`). No external registry access needed for agent images.

2. **Base images:** The gateway (`ghcr.io/nvidia/openshell/gateway:latest`) and supervisor (`ghcr.io/nvidia/openshell/supervisor:latest`) images must be pulled from ghcr.io. On air-gapped clusters, these should be mirrored to the internal registry.

3. **Sandbox base image:** The `ghcr.io/nvidia/openshell-community/sandboxes/base:latest` image (1.1GB) should be pre-pulled. Our fulltest script creates a pre-pull Job on OCP and uses `kind load docker-image` on Kind.

**Recommended image mirror strategy:**
```bash
# Mirror OpenShell images to internal registry
oc image mirror ghcr.io/nvidia/openshell/gateway:latest \
  ${INTERNAL_REGISTRY}/openshell-system/gateway:latest
oc image mirror ghcr.io/nvidia/openshell/supervisor:latest \
  ${INTERNAL_REGISTRY}/openshell-system/supervisor:latest
oc image mirror ghcr.io/nvidia/openshell-community/sandboxes/base:latest \
  ${INTERNAL_REGISTRY}/openshell-system/sandbox-base:latest
```

The Helm chart should accept image registry overrides so that all image references can be redirected to the internal registry.

### Question 4: Supervisor binary versioning

**Proposal asks:**
> With the init container approach, which image tag/version of the supervisor should be pinned? Should it match the gateway version exactly, or is there a compatibility matrix?

**Answer from codebase research:**

The current state is **immature** from a versioning perspective:
- Workspace version in `Cargo.toml` is `0.0.0`
- All images use `:latest` tag
- No version negotiation in the gRPC protocol
- Supervisor-gateway compatibility relies on matching protobuf definitions

**Our assessment:**
- **Tight coupling exists:** The supervisor fetches configuration from the gateway via gRPC (`GetSandboxSettings`, `GetProviderEnvironment`, `GetInferenceBundle`). If the protobuf schema changes, supervisor and gateway must be updated together.
- **Pin to the same version:** Until explicit version negotiation exists, supervisor and gateway versions should always match. The init container approach makes this easy — the init container image tag is set in the Helm chart alongside the gateway image tag.
- **Recommended tagging strategy:**
  - Use git SHA or release tags instead of `:latest` for reproducibility
  - The Kagenti operator could enforce version consistency by deploying matched supervisor+gateway versions

**PoC status:** We use `:latest` for both gateway and supervisor. This is acceptable for PoC but should be pinned for production.

### Question 5: Persistent sandboxes on OpenShift

**Proposal asks:**
> PVCs survive pod deletion, but do Sandbox CRDs survive gateway restarts? How does the gateway reconcile orphaned CRDs on startup?

**Answer from codebase research:**

The gateway spawns watchers during startup (`state.compute.spawn_watchers()`) that observe Sandbox CRDs. The `SandboxIndex` is rebuilt from the current state of the Kubernetes API, not from persistent state. This means:

1. **Sandbox CRDs DO survive gateway restarts** — they are Kubernetes custom resources stored in etcd, independent of the gateway process.
2. **The gateway reconciles on startup** — the watch loop rebuilds the `SandboxIndex` by listing all existing Sandbox CRDs and correlating them with pods.
3. **PVCs are managed independently** — they are not automatically deleted when Sandbox CRDs are deleted. This is by design for the `--keep` use case where workspace data should persist.

**Risks identified:**
- **Orphaned PVCs:** If a Sandbox CRD is deleted but the PVC is not, storage accumulates. No automatic cleanup exists.
- **Index rebuild latency:** During the window between gateway startup and watch loop completion, existing sandboxes are unreachable.
- **No finalizers:** The Sandbox CRD does not appear to have finalizers, so dependent resources (PVCs, Secrets) may not be cleaned up on deletion.

**Our assessment for OCP:**
- OCP's storage quotas can limit PVC accumulation per namespace
- A CronJob-based cleanup for orphaned PVCs is recommended for production
- The Kagenti operator could add finalizers to Sandbox CRDs to ensure proper cleanup

### Additional: Session Persistence (dtach)

**Proposal recommends:**
> Deliver dtach via the same init container that delivers the supervisor binary. Every sandbox gets detach/reattach capability without custom images.

**Our PoC perspective:**

Our agents are stateless A2A services, not interactive CLI sessions. Session persistence via dtach is relevant only for the CLI+SSH path (interactive Claude Code sandboxes). For our A2A agent model:

- **Not needed for A2A agents:** They are request-response services, not long-running interactive sessions.
- **Needed for interactive sandboxes:** If we support the `openshell sandbox create -- claude` use case, dtach is essential.
- **Recommendation:** Include dtach in the init container image alongside the supervisor binary, but only for the built-in sandbox mode (Mode 2 in our architecture doc).

### Additional: Headless Mode / AgentTask CRD

**Proposal introduces:**
> Build a standalone Kubernetes controller outside OpenShell. The AgentTask CRD defines headless workloads.

**Our PoC perspective:**

This is directly relevant to the Kagenti operator. The proposal's AgentTask CRD maps closely to Kagenti's existing `AgentRuntime` CRD concept:

| AgentTask CRD (proposal) | AgentRuntime CRD (Kagenti) |
|--------------------------|---------------------------|
| Image, entrypoint | Deployment template |
| Workspace PVC | PVC in deployment |
| Timeout, resource limits | Resource limits in deployment |
| OPA policy | Policy ConfigMap |
| Output configuration | OTel traces, MLflow |

**Recommendation:** Extend the Kagenti `AgentRuntime` CRD to support headless agent tasks rather than creating a separate `AgentTask` CRD. This avoids CRD proliferation and leverages the existing operator reconciliation loop.

---

## Session Persistence Architecture — How Context Continuity Works

### The insight: context lives in the backend, not the agent

The proposal assumes agents are interactive CLI tools accessed via SSH. Our PoC demonstrates a different model: agents are A2A services, and the **Kagenti backend** is the session management layer.

This means **context continuity does not require agent-side changes**. The architecture:

1. **Kagenti Backend** stores conversation history in PostgreSQL (already implemented)
2. When a user sends a message:
   - Backend looks up the session (conversation history)
   - Sends the message to the agent (via A2A for custom agents, via `ExecSandbox` gRPC or `kubectl exec` for builtin sandbox agents)
   - Gets the response
   - Stores the updated conversation in PostgreSQL
   - Returns the response to the UI
3. **Workspace PVC** preserves files (code, configs) across pod restarts
4. **Agent process state** (in-memory context, dtach socket) lives in the pod and is lost on restart — but the conversation history is in the backend

### Communication channels per agent type

| Agent Type | Protocol | Session Store | Workspace |
|-----------|----------|--------------|-----------|
| Custom A2A (weather, ADK, Claude SDK) | A2A `message/send` JSON-RPC | Kagenti backend PostgreSQL | N/A (stateless services) |
| OpenShell builtin (Claude Code, OpenCode) | `ExecSandbox` gRPC or `kubectl exec` | Kagenti backend PostgreSQL | PVC-backed `/workspace` |
| OpenShell builtin (interactive SSH) | CLI → gateway → SSH tunnel | Gateway SQLite/PostgreSQL | PVC-backed `/workspace` |

### Key discovery: `ExecSandbox` gRPC RPC

The OpenShell gateway exposes an `ExecSandbox` RPC that supports:
- Command execution with stdin/stdout
- Environment overrides
- Timeout configuration
- Pseudo-terminal support

This means the **Kagenti backend can interact with builtin sandbox agents without an A2A wrapper**. The backend calls `ExecSandbox` to send prompts to Claude Code or OpenCode, captures the response, and stores it in the session.

### What this means for the tests

| Test Scenario | Custom A2A Agents | Builtin Sandbox Agents |
|--------------|-------------------|----------------------|
| Multi-turn conversation | A2A `message/send` with `contextId` | `kubectl exec` with sequential commands |
| Context isolation | Separate A2A requests | Separate sandbox CRs |
| Context continuity | `contextId` in A2A (needs upstream) OR backend session store | Backend session store + workspace PVC |
| Scale-down/up persistence | Backend restores context from PostgreSQL | Backend restores context + PVC restores workspace |
| Workspace persistence | N/A | PVC verified via `kubectl exec cat` |

### What's NOT needed

- **A2A wrapper inside builtin sandboxes**: Not needed. `ExecSandbox` gRPC is the bridge.
- **Agent-side session storage**: Not needed. Backend handles it.
- **Upstream ADK contextId fix**: Nice to have, but not blocking. Backend can manage context externally.
- **dtach for session resume**: Needed only for interactive CLI sessions, not for API-driven interactions.

### What IS needed (future work)

1. **Kagenti backend integration with OpenShell gateway gRPC**: Backend calls `ExecSandbox` to interact with builtin sandbox agents
2. **PVC inspection in the UI**: The Kagenti UI has `FileBrowser` and `FilePreview` components — wire them to browse sandbox workspace PVCs
3. **Session restore on sandbox recreation**: When a sandbox is recreated with the same PVC, the backend loads the previous conversation from PostgreSQL and the workspace from the PVC

---

## Recommended Next Steps

1. **Review this alignment document** in the PR and discuss the 7 design questions and 5 proposal questions.
2. **Test reduced SCC privileges** — validate that `NET_ADMIN + SYS_ADMIN + SYS_PTRACE` work without `privileged: true` on both Kind and OCP.
3. **Decide on supervisor delivery mechanism** — init container + emptyDir (proposal) vs bake-into-image (current PoC).
4. **Pin image versions** — replace `:latest` with git SHA-based tags for reproducibility.
5. **Plan Phase 2 integration priority** — recommend starting with Keycloak provider credential integration as it has the highest multi-user impact.
6. **Consider extending AgentRuntime CRD** for headless agent tasks instead of creating a separate AgentTask CRD.
