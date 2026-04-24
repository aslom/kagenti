# Claude SDK Agent

> **Type:** Custom A2A
> **Framework:** Anthropic SDK / OpenAI-compatible (httpx)
> **LLM:** LiteMaaS (llama-scout-17b)
> **Supervisor:** No
> **Sandbox Model:** Mode 1 (Kagenti Deployment)
> **Status:** Deployed, tested (Kind + HyperShift)

## 1. Overview

Code review agent using the Anthropic Python SDK with automatic format switching.
When `ANTHROPIC_BASE_URL` points to a non-Anthropic endpoint (e.g., LiteMaaS),
uses httpx with OpenAI chat/completions format. A2A protocol implemented manually
via Starlette (Anthropic SDK has no built-in A2A wrapper unlike Google ADK).

## 2. Architecture

```mermaid
graph LR
    Client["Test / UI"] -->|"A2A message/send"| Agent["Claude SDK Agent<br/>:8080 (Starlette)"]
    Agent -->|"auto-detect format"| Switch{{"anthropic.com?"}}
    Switch -->|"Yes"| Anthropic["Anthropic SDK<br/>/v1/messages"]
    Switch -->|"No"| HTTPX["httpx<br/>/v1/chat/completions"]
    HTTPX --> LLM["LiteMaaS"]
```

## 3. Files

```
deployments/openshell/agents/claude-sdk-agent/
├── agent.py              # Starlette A2A server + Anthropic/OpenAI client
├── Dockerfile            # python:3.12-slim
├── deployment.yaml       # Deployment + Service + AgentRuntime CR
├── policy-data.yaml      # OPA policy
├── sandbox-policy.rego   # OPA Rego rules
└── requirements.txt      # anthropic, starlette, uvicorn, httpx
```

## 4. Deployment

Same as adk-agent (docker build + kind load, or OCP binary build).

## 5. Capabilities

| Capability | Supported | Notes |
|-----------|-----------|-------|
| A2A protocol | **Yes** | Manual Starlette implementation |
| Multi-turn context | **No** | Stateless — each request independent |
| Tool calling | No | LLM prompt-based only |
| Subagent delegation | No | Single-purpose agent |
| Memory/knowledge | No | No persistent state |
| Skill execution | **Via prompt** | Skill markdown injected into system prompt |
| HITL approval | L0 | OPA policy mounted, not enforced |

## 6. Kagenti Integration

### 6.1 Communication Adapter
A2A JSON-RPC (already implemented).

### 6.2 Session Management
None — stateless. Backend stores turns in PostgreSQL.

### 6.3 Observable Events

| Event | Source | Kagenti UI Component | Phase |
|-------|--------|---------------------|-------|
| LLM response | A2A response artifacts | AgentChat | Current |
| Error (LLM unavailable) | Generic error message | AgentChat | Current |

### 6.4 FileBrowser Integration
N/A — no workspace.

## 7. LLM Compatibility

| Provider | Protocol | Works? | Notes |
|----------|----------|--------|-------|
| LiteMaaS | OpenAI-compat | **Yes** | Auto-detected via base_url |
| Anthropic API | Claude messages | **Yes** | Native SDK |
| Budget Proxy | OpenAI-compat | **Yes** | Default config |

## 8. Testing Status

| Test File | Tests | Pass | Skip | Notes |
|-----------|-------|------|------|-------|
| test_02_a2a_connectivity | 2 | 2 | 0 | Hello + agent card |
| test_05_multiturn | 3 | 2 | 1 | Sequential + isolation pass; continuity skips |
| test_07_skill_execution | 7 | 5 | 2 | PR review, RCA, security, real GH PR, RCA logs |

## 9. Sandbox Deployment Models

| Model | Supported | Notes |
|-------|-----------|-------|
| Mode 1: Kagenti Deployment | **Current** | Standard Deployment + Service |
| Mode 1 + Supervisor | Possible | Would enable OPA enforcement |
| Mode 2: Sandbox CR | Not applicable | Custom code, not a CLI agent |
