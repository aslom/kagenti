# OpenShell Agent Deployment Quick Reference

> Back to [main doc](openshell-integration.md) | **Detailed docs:** [agents/README.md](agents/README.md)

## Agent Summary

| Agent | Tier | LLM | Supervisor | Skills | Docs |
|-------|------|-----|-----------|--------|------|
| `weather_agent` | 3 | No | No | N/A | [weather-agent.md](agents/weather-agent.md) |
| `adk_agent` | 3 | LiteMaaS | No | 4/4 pass | [adk-agent.md](agents/adk-agent.md) |
| `claude_sdk_agent` | 3 | LiteMaaS | No | 6/6 pass | [claude-sdk-agent.md](agents/claude-sdk-agent.md) |
| `adk_agent_supervised` | **2** | LiteMaaS | **Yes** | 3/3 pass | [adk-agent.md](agents/adk-agent.md) (supervised variant) |
| `weather_supervised` | 2 | No | Yes | N/A | [weather-supervised.md](agents/weather-supervised.md) |
| `openshell_claude` | 1 | Anthropic | Yes | Blocked | [openshell-claude.md](agents/openshell-claude.md) |
| `openshell_opencode` | 1 | LiteMaaS | Yes | 3/3 pass | [openshell-opencode.md](agents/openshell-opencode.md) |
| `openshell_generic` | 1 | N/A | Yes | N/A | — |

## Quick Deploy (Kind)

```bash
# Full stack (cluster + platform + gateway + agents + tests)
.github/scripts/local-setup/openshell-full-test.sh --skip-cluster-destroy

# Agents only (cluster already running)
.github/scripts/local-setup/openshell-build-agents.sh
kubectl apply -f deployments/openshell/agents/*/deployment.yaml
kubectl apply -f deployments/openshell/agents/*.yaml
```

## Quick Deploy (OCP/HyperShift)

```bash
export KUBECONFIG=~/clusters/hcp/<cluster>/auth/kubeconfig
.github/scripts/local-setup/openshell-full-test.sh \
  --platform ocp --skip-cluster-create --skip-cluster-destroy --skip-install
```

## Agent Deployment Files

```
deployments/openshell/agents/
├── weather-agent.yaml                    # Tier 3: LangGraph + MCP
├── adk-agent/
│   ├── agent.py + Dockerfile + deployment.yaml   # Tier 3: Google ADK + LiteLLM
│   └── policy-data.yaml + sandbox-policy.rego
├── claude-sdk-agent/
│   ├── agent.py + Dockerfile + deployment.yaml   # Tier 3: Anthropic SDK + OpenAI-compat
│   └── policy-data.yaml + sandbox-policy.rego
├── adk-agent-supervised/
│   ├── Dockerfile + deployment.yaml              # Tier 2: ADK + supervisor + port-bridge
│   └── policy-data.yaml + sandbox-policy.rego
└── weather-agent-supervised/
    ├── Dockerfile + deployment.yaml              # Tier 2: Weather + supervisor + port-bridge
    └── policy-data.yaml + sandbox-policy.rego
```

## Key Configuration

All agents use `kagenti.io/inject: disabled` to prevent AuthBridge sidecar
injection (Phase 3 will resolve the netns architectural conflict).

Supervised agents (Tier 2) have a `port-bridge` sidecar (`python:3.12-slim`)
that bridges `Pod:8080 → 10.200.0.2:8080` across the supervisor's netns.
