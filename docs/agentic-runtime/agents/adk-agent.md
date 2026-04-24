# ADK Agent (Google Agent Development Kit)

> Back to [agent catalog](README.md) | [main doc](../openshell-integration.md)

> **Type:** Custom A2A
> **Framework:** Google ADK + LiteLLM
> **LLM:** LiteMaaS (llama-scout-17b)
> **Supervisor:** No
> **Sandbox Model:** Mode 1 (Kagenti Deployment)
> **Status:** Deployed, tested (Kind + HyperShift)

## 1. Overview

PR review agent built with Google's Agent Development Kit (ADK). Uses the
`to_a2a()` wrapper to expose the agent via A2A JSON-RPC protocol. LLM calls
route through LiteLLM (OpenAI-compatible format) to LiteMaaS or Budget Proxy.

## 2. Architecture

```mermaid
graph LR
    Client["Test / UI"] -->|"A2A message/send"| Agent["ADK Agent<br/>:8080"]
    Agent -->|"to_a2a()"| ADK["Google ADK<br/>LlmAgent"]
    ADK -->|"OpenAI-compat"| LLM["LiteMaaS<br/>llama-scout-17b"]
    Agent -->|"policy mounted"| Policy["/etc/openshell/"]
```

## 3. Files

```
deployments/openshell/agents/adk-agent/
├── agent.py              # LlmAgent + review_pr tool + to_a2a() wrapper
├── Dockerfile            # python:3.12-slim
├── deployment.yaml       # Deployment + Service + AgentRuntime CR
├── policy-data.yaml      # OPA filesystem + network rules
├── sandbox-policy.rego   # OPA Rego deny/allow rules
└── requirements.txt      # google-adk, a2a-sdk, litellm
```

## 4. Deployment

```bash
# Kind
docker build -t adk-agent:latest deployments/openshell/agents/adk-agent/
kind load docker-image adk-agent:latest --name kagenti

# OCP (binary build)
oc -n team1 new-build --binary --strategy=docker --name=adk-agent
oc -n team1 start-build adk-agent --from-dir=deployments/openshell/agents/adk-agent/ --follow

kubectl apply -f deployments/openshell/agents/adk-agent/deployment.yaml
```

## 5. Capabilities

| Capability | Supported | Notes |
|-----------|-----------|-------|
| A2A protocol | **Yes** | Native via `to_a2a()` — auto-generates agent card |
| Multi-turn context | **Partial** | Returns `contextId` but creates new one per request (upstream gap) |
| Tool calling | **Yes** | `review_pr` tool registered with LlmAgent |
| Subagent delegation | **Yes** (ADK native) | ADK supports agents-as-tools, not yet used in PoC |
| Memory/knowledge | **In-memory** | ADK SessionService tracks session state; lost on pod restart |
| Skill execution | **Via prompt** | Kagenti skill markdown injected into LLM prompt |
| HITL approval | **L0** | OPA policy mounted but not enforced without supervisor |

### ADK-Specific Features

| ADK Feature | Available | Used in PoC? | Notes |
|------------|-----------|-------------|-------|
| `to_a2a()` wrapper | Yes | Yes | Auto A2A agent card + endpoint |
| SessionService | Yes | Implicit | In-memory sessions via to_a2a |
| ToolConfirmation (HITL) | Yes | No | ADK native pause-for-approval |
| RunState persistence | Yes | No | Snapshot + resume after approval |
| Multi-agent composition | Yes | No | Agents-as-tools pattern |
| Event streaming | Yes | No | Structured Event objects |
| DatabaseSessionService | Yes | No | Persistent sessions (PostgreSQL/SQLite) |
| Auto context windowing | Yes | Yes | Token budget management |

## 6. Kagenti Integration

### 6.1 Communication Adapter
**A2A JSON-RPC** (already implemented). The ADK `to_a2a()` wrapper handles
protocol translation natively.

### 6.2 Session Management
ADK provides `SessionService` with in-memory storage by default. For
persistent sessions, switch to `DatabaseSessionService` backed by
PostgreSQL or SQLite.

**Current:** In-memory (lost on restart)
**Target:** DatabaseSessionService → Kagenti PostgreSQL

### 6.3 Observable Events

| Event | Source | Kagenti UI Component | Phase |
|-------|--------|---------------------|-------|
| LLM request/response | ADK Event history | PromptInspector | Phase 2 |
| Tool call (review_pr) | ADK function_call Event | EventsPanel | Phase 2 |
| Tool result | ADK function_response Event | EventsPanel | Phase 2 |
| Token usage | ADK session metrics | LlmUsagePanel | Phase 2 |
| Context windowing | ADK auto-compression | SessionStatsPanel | Phase 3 |
| HITL approval (future) | ADK ToolConfirmation | HitlApprovalCard | Phase 3 |

### 6.4 FileBrowser Integration
N/A — custom A2A agent with no persistent workspace. If PVC is added,
ADK's session state could be browsed.

## 7. LLM Compatibility

| Provider | Protocol | Works? | Notes |
|----------|----------|--------|-------|
| LiteMaaS | OpenAI-compat | **Yes** | Current PoC config via `OPENAI_API_BASE` |
| Budget Proxy | OpenAI-compat | **Yes** | Default deployment config |
| Ollama | OpenAI-compat | **Yes** | For local Kind testing |
| Anthropic API | Claude messages | No | ADK uses OpenAI format |

## 8. Policy Configuration

```yaml
filesystem_policy:
  read_only: [/usr, /lib, /lib64, /etc, /home, /bin, /sbin]
  read_write: [/tmp, /app, /root, /var/log]
network_policies:
  internal:
    endpoints:
      - host: "*.svc.cluster.local"
        port: 8080
      - host: "*.svc.cluster.local"
        port: 443
  litemaas:
    endpoints:
      - host: "*.redhatworkshops.io"
        port: 443
```

## 9. Testing Status

| Test File | Tests | Pass | Skip | Notes |
|-----------|-------|------|------|-------|
| test_02_a2a_connectivity | 2 | 2 | 0 | Hello + agent card |
| test_05_multiturn | 3 | 2 | 1 | Sequential + isolation pass; continuity skips |
| test_07_skill_execution | 5 | 3 | 2 | PR review, RCA, security pass; real GH PR pass |
| test_03_credential_security | 4 | 4 | 0 | secretKeyRef, no hardcoded keys |
| test_06_conversation_resume | 2 | 0 | 2 | Destructive-gated |

## 10. Sandbox Deployment Models

| Model | Supported | Notes |
|-------|-----------|-------|
| Mode 1: Kagenti Deployment | **Current** | Standard Deployment + Service |
| Mode 1 + Supervisor | Possible | Add supervisor; enables OPA enforcement |
| Mode 2: Sandbox CR | Not applicable | Not a builtin CLI agent |

### Future: ADK HITL Integration

ADK's `ToolConfirmation` pattern natively supports HITL:
1. Mark sensitive tools with `needsApproval: true`
2. ADK pauses execution and snapshots `RunState`
3. Kagenti backend receives pause event
4. `HitlApprovalCard` shown in UI
5. Human approves/rejects
6. ADK resumes from snapshot

This maps directly to HITL Level L3 (sync approval) and is the most
natural HITL integration point across all agent types.
