# OpenShell PoC: Design Spec

> **Date:** 2026-04-16
> **Status:** Spec
> **Branch:** `feat/openshell-poc`

## Goal

Deploy OpenShell (upstream, with K8s compute driver) alongside Kagenti
Operator on Kind and HyperShift, with three test agents (Weather, Google
ADK, OpenCode) and E2E tests. Prove the systems coexist and supervisor
isolation works.

**PoC scope only** — no UI, no Backend API, no sandbox agent (Legion).

## Components

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| OpenShell Gateway | `openshell-system` | Sandbox control plane |
| K8s Compute Driver | `openshell-system` | Creates sandbox pods |
| Kagenti Operator | `kagenti-system` | AgentRuntime CRs |
| Keycloak | `keycloak` | OIDC |
| SPIRE | `spire-system` | Workload identity |
| Istio Ambient | `istio-system` | mTLS |
| LiteLLM | `kagenti-system` | LLM routing |
| Budget Proxy | `team1` | Token budget |
| PostgreSQL | `team1` | Sessions |
| Weather Agent | `team1` | Simple A2A agent |
| Google ADK Agent | `team1` | ADK framework agent |
| OpenCode Agent | `team1` | OpenCode agent |

## Test Agents

- **Weather Agent** — existing, no LLM, structured responses
- **Google ADK Agent** — new adapter, LLM-powered, PR review skill
- **OpenCode Agent** — existing WIP adapter, code generation skill

## E2E Tests

```
kagenti/tests/e2e/openshell/
├── conftest.py
├── test_platform_health.py         # All components running
├── test_weather_agent.py           # Conversation + multi-city
├── test_adk_agent.py               # Conversation + PR review skill
├── test_opencode_agent.py          # Conversation + code gen
├── test_sandbox_lifecycle.py       # Create/list/delete sandboxes
├── test_credential_isolation.py    # Placeholder env vars
└── test_supervisor_isolation.py    # Process tree, Landlock, seccomp
```

## Deployment

- **Env file:** `deployments/envs/dev_values_openshell.yaml`
- **Fulltest:** `.github/scripts/local-setup/openshell-full-test.sh`
- **OpenShell:** Raw manifests in `deployments/openshell/`

## CI

| Workflow | Trigger | Voting? |
|----------|---------|---------|
| `e2e-openshell-kind.yaml` | Push to `feat/openshell-*` + manual | No |
| `e2e-openshell-hypershift.yaml` | `/run-e2e-openshell` + manual | No |

## Success Criteria

1. All pods running on Kind
2. Three agents respond to A2A conversations
3. ADK agent performs PR review skill
4. Supervisor isolation verified
5. Sandbox lifecycle works via OpenShell Gateway
