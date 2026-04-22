# OpenShell Integration with Kagenti

> **Status:** PoC (experimental)
> **Source:** [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell) (Apache 2.0)

---

## 1. Overview

[OpenShell](https://github.com/NVIDIA/OpenShell) is a sandbox runtime for autonomous AI agents
providing kernel-level isolation (Landlock, seccomp, network namespaces) with OPA/Rego policy
enforcement and zero-secret credential isolation. Kagenti integrates OpenShell to provide
agent process isolation alongside its existing platform services (identity, budget, observability).

## 2. Current PoC Architecture

The PoC deploys OpenShell (upstream, with K8s compute driver) alongside Kagenti Operator.
No UI or Backend API — E2E tests call agents directly via A2A.

```mermaid
graph TB
    subgraph tests["E2E Tests"]
        T["pytest — A2A calls"]
    end

    subgraph openshell_ns["openshell-system"]
        GW["OpenShell Gateway"]
        KD["K8s Compute Driver"]
    end

    subgraph kagenti_ns["kagenti-system"]
        OP["Kagenti Operator"]
        LLM["LiteLLM :4000"]
        KC["Keycloak"]
    end

    subgraph agent_ns["team1"]
        subgraph w_pod["Weather Agent"]
            W_SV["Supervisor"] --> W_AG["weather app"]
        end
        subgraph a_pod["ADK Agent"]
            A_SV["Supervisor"] --> A_AG["Google ADK agent"]
        end
        subgraph o_pod["OpenCode Agent"]
            O_SV["Supervisor"] --> O_AG["opencode"]
        end
        BP["Budget Proxy"]
        PG["PostgreSQL"]
    end

    T -->|"A2A"| w_pod & a_pod & o_pod
    W_SV & A_SV & O_SV -->|"gRPC"| GW
    GW --> KD
    A_AG & O_AG -->|"via supervisor proxy"| BP --> LLM
    OP -->|"AgentRuntime CR"| w_pod & a_pod & o_pod
```

### Components

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| OpenShell Gateway | `openshell-system` | Sandbox control plane |
| K8s Compute Driver | `openshell-system` | Creates sandbox pods via K8s API |
| Kagenti Operator | `kagenti-system` | AgentRuntime CRs, webhook injection |
| Keycloak | `keycloak` | OIDC provider |
| SPIRE | `spire-system` | Workload identity (SPIFFE) |
| Istio Ambient | `istio-system` | mTLS mesh |
| LiteLLM | `kagenti-system` | LLM model routing |
| Budget Proxy | `team1` | LLM token budget enforcement |
| PostgreSQL | `team1` | Sessions + budget databases |

## 3. Two Agent Deployment Modes

Kagenti supports two agent deployment modes that coexist:

### Mode 1: Custom Agents (Kagenti-managed)

Custom A2A agents deployed as K8s Deployments. Used for production agents
with custom code, frameworks (LangGraph, ADK), and A2A protocol.

```mermaid
graph LR
    Wizard["Wizard / kubectl"] -->|"creates"| Dep["Deployment + Service"]
    Dep --> Pod1["Agent Pod<br/>(custom image)"]
    OP1["Kagenti Operator"] -->|"AgentRuntime CR"| Pod1
```

- **Image:** Custom Dockerfile per agent
- **Interaction:** A2A JSON-RPC 2.0 (programmatic)
- **Lifecycle:** Long-running Deployment
- **Examples:** Weather agent, ADK agent, Claude SDK agent

### Mode 2: Built-in Sandboxes (OpenShell-managed)

Pre-installed CLI agents created via OpenShell gateway. The base sandbox
image (`ghcr.io/nvidia/openshell-community/sandboxes/base:latest`, ~620MB)
includes Claude CLI, OpenCode, Codex, Copilot, Python, Node.js, git.

```mermaid
graph LR
    CLI2["openshell sandbox create<br/>-- claude"] -->|"gRPC"| GW2["Gateway"]
    GW2 -->|"K8s driver"| Pod2["Sandbox Pod<br/>(base image + supervisor)"]
    Pod2 --> Agent2["claude CLI<br/>(pre-installed)"]
```

- **Image:** `ghcr.io/nvidia/openshell-community/sandboxes/base:latest` (all CLIs pre-installed)
- **Interaction:** SSH exec (interactive terminal) or `openshell sandbox exec`
- **Lifecycle:** Ephemeral sandbox (TTL, on-demand create/destroy)
- **CRD:** Sandbox CR (`agents.x-k8s.io`)

### Pre-installed CLIs in Base Image

| CLI | LLM Protocol | Works with LiteLLM? | Required Key |
|-----|-------------|---------------------|-------------|
| **claude** | Anthropic `/v1/messages` | Yes (inference router) | Anthropic or LiteLLM virtual key |
| **opencode** | OpenAI `/v1/chat/completions` | Yes (native format) | OpenAI-compatible key |
| **codex** | OpenAI-specific | Partial | OpenAI API key |
| **copilot** | GitHub Copilot API | No (proprietary) | GitHub Copilot subscription |

### Architecture with Both Modes

```mermaid
graph TB
    subgraph openshell_ns2["openshell-system"]
        GW3["OpenShell Gateway"]
        KD3["K8s Compute Driver"]
    end

    subgraph kagenti_ns2["kagenti-system"]
        OP3["Kagenti Operator"]
    end

    subgraph agent_ns2["team1"]
        subgraph custom["Custom Agents (Mode 1)"]
            W["Weather Agent<br/>(Deployment)"]
            ADK3["ADK Agent<br/>(Deployment)"]
        end
        subgraph builtin["Built-in Sandboxes (Mode 2)"]
            CS["Claude Sandbox<br/>(Sandbox CR)"]
            OC["OpenCode Sandbox<br/>(Sandbox CR)"]
        end
    end

    OP3 -->|"AgentRuntime CR"| custom
    GW3 -->|"Sandbox CR"| builtin
    KD3 -->|"creates pods"| builtin
```

Both modes share the same namespace, Budget Proxy, PostgreSQL, and Istio mesh.
Custom agents use A2A protocol; built-in sandboxes use SSH/exec.

---

## 5. Supervisor as Container Entrypoint

Each agent pod uses the OpenShell supervisor as the container entrypoint:

1. Supervisor starts (`ENTRYPOINT`)
2. Connects to OpenShell Gateway via `OPENSHELL_GATEWAY` env var
3. Reads OPA/Rego policy
4. Applies Landlock (filesystem restrictions) + custom seccomp (syscall filtering)
5. Drops all capabilities
6. Execs the agent process as a restricted child

The agent inherits kernel-enforced isolation for its entire lifetime. Normal pod
networking is preserved (no network namespace in PoC), so Istio mesh works unchanged.

## 6. Credential Isolation

OpenShell implements zero-secret credential isolation. Agent env vars contain
**placeholder tokens** (`openshell:resolve:env:API_KEY`), not real secrets. The
supervisor proxy resolves placeholders to real credentials at the HTTP layer
via TLS termination before forwarding upstream.

For LLM calls, the supervisor's inference router strips agent-supplied auth
headers entirely and injects backend API keys from the gateway's credential store.

## 7. Target Architecture (Phase 2)

Phase 2 introduces Kagenti as an OpenShell compute driver, implementing the
`ComputeDriver` gRPC interface ([PR #817](https://github.com/NVIDIA/OpenShell/pull/817),
merged). OpenShell gateway manages sandbox lifecycle; Kagenti provisions pods
with platform infrastructure (Budget Proxy, AgentRuntime CR, workspace PVC).

```mermaid
graph TB
    subgraph gw["OpenShell Gateway"]
        G["Gateway"]
        KCD["Kagenti Compute Driver"]
        G -->|"ComputeDriver gRPC"| KCD
    end

    subgraph kagenti["Kagenti Platform"]
        KCD -->|"creates"| Pod["Agent Pod<br/>(supervisor entrypoint<br/>+ Budget Proxy config)"]
        BE["Kagenti Backend"] -->|"A2A"| Pod
    end
```

## 8. OpenShell RFC 0001

OpenShell is being rearchitected via [RFC 0001](https://github.com/NVIDIA/OpenShell/pull/836)
into a composable, driver-based system with four pluggable subsystems:

| Subsystem | Purpose | Kagenti Mapping |
|-----------|---------|-----------------|
| **Compute** | Sandbox lifecycle (K8s, Podman, VM) | Kagenti as compute driver (phase 2) |
| **Credentials** | Secret resolution (Vault, K8s Secrets) | Delivers secrets to supervisor proxy |
| **Control-plane identity** | User/operator auth (mTLS, OIDC) | Keycloak OIDC |
| **Sandbox identity** | Workload identity (SPIFFE) | SPIRE |

## 9. Related PRs

Key upstream PRs relevant to integration:

| PR | Status | Impact |
|----|--------|--------|
| [#817](https://github.com/NVIDIA/OpenShell/pull/817) | Merged | K8s compute driver extraction |
| [#836](https://github.com/NVIDIA/OpenShell/pull/836) | Open | RFC 0001 — core architecture |
| [#858](https://github.com/NVIDIA/OpenShell/pull/858) | Open | VM compute driver (proves out-of-process driver) |
| [#822](https://github.com/NVIDIA/OpenShell/pull/822) | Merged | L7 deny rules in policy schema |
| [#860](https://github.com/NVIDIA/OpenShell/pull/860) | Open | Incremental policy updates |
| [#861](https://github.com/NVIDIA/OpenShell/pull/861) | Open | Supervisor session relay |
