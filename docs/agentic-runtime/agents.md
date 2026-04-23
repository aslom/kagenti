# OpenShell Agent Catalog

> Back to [main doc](openshell-integration.md)

Each agent section follows a consistent format: overview, files, deployment,
testing, policy configuration, and sandboxing features.

---

## weather_agent

**Type:** Custom A2A | **Framework:** LangGraph | **LLM:** None (MCP tool) | **Supervisor:** No

### Overview
Stateless weather query agent using the Open-Meteo API via MCP weather-tool.
No LLM required — demonstrates pure tool-calling A2A pattern.

### Files
```
deployments/openshell/agents/weather-agent.yaml     # Deployment + Service + AgentRuntime CR + policy ConfigMap
```

### Deployment
```bash
kubectl apply -f deployments/openshell/agents/weather-agent.yaml
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=weather-agent -n team1 --timeout=120s
```

The weather agent uses a public image (`ghcr.io/kagenti/agent-examples/weather_service:latest`).
No build step required.

### Testing
```bash
# A2A query
curl -s -X POST http://localhost:8080/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"messageId":"m1","role":"user","parts":[{"type":"text","text":"Weather in London?"}]}}}'
```

### Policy
```yaml
# In weather-agent.yaml ConfigMap
network_policies:
  internal:
    endpoints:
      - host: "*.svc.cluster.local"
        port: 8080
  weather_api:
    endpoints:
      - host: "api.open-meteo.com"
        port: 443
```

Policy is mounted at `/etc/openshell/policy.yaml` but **not enforced** (no supervisor).

### Sandboxing Features
| Feature | Status |
|---------|--------|
| Landlock filesystem | Not active (no supervisor) |
| Seccomp BPF | Not active |
| Network namespace | Not active |
| OPA egress policy | Mounted, not enforced |
| Credential isolation | K8s secretKeyRef only |
| securityContext | `allowPrivilegeEscalation: false`, `drop: [ALL]` |

---

## adk_agent

**Type:** Custom A2A | **Framework:** Google ADK + LiteLLM | **LLM:** LiteMaaS (llama-scout) | **Supervisor:** No

### Overview
PR review agent built with Google ADK. Uses LiteLLM wrapper to route LLM
calls through Budget Proxy or LiteMaaS. Exposes A2A via `to_a2a()`.

### Files
```
deployments/openshell/agents/adk-agent/
├── agent.py              # Agent code (LlmAgent + review_pr tool)
├── Dockerfile            # python:3.12-slim
├── deployment.yaml       # Deployment + Service + AgentRuntime CR
├── policy-data.yaml      # OPA policy (filesystem + network rules)
├── sandbox-policy.rego   # OPA Rego rules
└── requirements.txt      # google-adk, a2a-sdk
```

### Deployment
```bash
# Kind: build + load
docker build -t adk-agent:latest deployments/openshell/agents/adk-agent/
kind load docker-image adk-agent:latest --name kagenti

# OCP: binary build
oc -n team1 new-build --binary --strategy=docker --name=adk-agent
oc -n team1 start-build adk-agent --from-dir=deployments/openshell/agents/adk-agent/ --follow

# Deploy
kubectl apply -f deployments/openshell/agents/adk-agent/deployment.yaml
```

### LLM Configuration
The deployment YAML defaults to Budget Proxy. Override for LiteMaaS:
```bash
kubectl set env deploy/adk-agent -n team1 \
  OPENAI_API_BASE=https://litellm-prod.apps.maas.redhatworkshops.io/v1 \
  LLM_MODEL=openai/llama-scout-17b
```

### Testing
```bash
# Hello (no LLM needed for basic A2A)
curl -s -X POST http://localhost:8080/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"messageId":"m1","role":"user","parts":[{"type":"text","text":"Say hello"}]}}}'

# PR review (requires LLM)
curl -s -X POST http://localhost:8080/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"messageId":"m1","role":"user","parts":[{"type":"text","text":"Review: def f(x): return eval(x)"}]}}}'
```

### Policy
```yaml
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

### Sandboxing Features
| Feature | Status |
|---------|--------|
| Landlock filesystem | Not active (no supervisor) |
| Seccomp BPF | Not active |
| Network namespace | Not active |
| OPA egress policy | Mounted, not enforced |
| Credential isolation | K8s secretKeyRef (`litellm-virtual-keys`) |
| securityContext | `allowPrivilegeEscalation: false`, `drop: [ALL]` |

---

## claude_sdk_agent

**Type:** Custom A2A | **Framework:** Anthropic SDK / OpenAI-compat | **LLM:** LiteMaaS (llama-scout) | **Supervisor:** No

### Overview
Code review agent using the Anthropic Python SDK. Auto-detects LLM endpoint
format: uses Anthropic SDK for anthropic.com, httpx with OpenAI format for
LiteMaaS or other OpenAI-compatible endpoints.

### Files
```
deployments/openshell/agents/claude-sdk-agent/
├── agent.py              # A2A server (Starlette + Anthropic/OpenAI client)
├── Dockerfile            # python:3.12-slim
├── deployment.yaml       # Deployment + Service + AgentRuntime CR
├── policy-data.yaml      # OPA policy
├── sandbox-policy.rego   # OPA Rego rules
└── requirements.txt      # anthropic, starlette, uvicorn, httpx
```

### Deployment
Same pattern as `adk_agent` (docker build + kind load, or OCP binary build).

### LLM Configuration
```bash
kubectl set env deploy/claude-sdk-agent -n team1 \
  ANTHROPIC_BASE_URL=https://litellm-prod.apps.maas.redhatworkshops.io/v1 \
  ANTHROPIC_MODEL=llama-scout-17b
```

### Testing
```bash
# Code review
curl -s -X POST http://localhost:8080/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"messageId":"m1","role":"user","parts":[{"type":"text","text":"Review: import pickle; pickle.load(open(f))"}]}}}'
```

### Sandboxing Features
Same as `adk_agent` — policy mounted but not enforced without supervisor.

---

## weather_supervised

**Type:** Custom A2A | **Framework:** LangGraph | **LLM:** None | **Supervisor:** Yes (Landlock + seccomp + netns + OPA)

### Overview
Weather agent running inside the OpenShell supervisor. Demonstrates all four
sandboxing layers active simultaneously. The supervisor is the container
entrypoint — it applies isolation before exec-ing the weather app.

### Files
```
deployments/openshell/agents/weather-agent-supervised/
├── Dockerfile            # Multi-stage: supervisor image + weather image
├── deployment.yaml       # Deployment + Service (privileged: true)
├── policy-data.yaml      # OPA policy (filesystem + network rules)
└── sandbox-policy.rego   # OPA Rego rules
```

### Deployment
```bash
# Requires building from both supervisor and weather images
docker build -t weather-agent-supervised:latest \
  deployments/openshell/agents/weather-agent-supervised/
kind load docker-image weather-agent-supervised:latest --name kagenti
kubectl apply -f deployments/openshell/agents/weather-agent-supervised/deployment.yaml

# OCP: grant privileged SCC to dedicated SA
kubectl create serviceaccount openshell-supervisor -n team1
oc adm policy add-scc-to-user privileged -z openshell-supervisor -n team1
```

### Testing
Port-forward is blocked by the supervisor's network namespace. Test via kubectl exec:
```bash
kubectl exec deploy/weather-agent-supervised -n team1 -- echo "alive"
kubectl logs deploy/weather-agent-supervised -n team1 | grep "CONFIG:APPLYING"
```

### Policy
```yaml
filesystem_policy:
  read_only: [/usr, /lib, /etc, /bin, /sbin]
  read_write: [/tmp, /app, /root, /var/log]
network_policies:
  internal:
    endpoints:
      - host: "*.svc.cluster.local"
        port: 8080
  litemaas:
    endpoints:
      - host: "*.redhatworkshops.io"
        port: 443
```

### Sandboxing Features
| Feature | Status |
|---------|--------|
| Landlock filesystem | **Active** — ABI V3, 14+ rules applied |
| Seccomp BPF | **Active** — dangerous syscalls blocked |
| Network namespace | **Active** — veth pair 10.200.0.1/10.200.0.2 |
| OPA egress policy | **Active** — HTTP CONNECT proxy + Rego evaluation |
| TLS MITM | **Active** — ephemeral CA for L7 inspection |
| Credential isolation | K8s secretKeyRef + supervisor proxy (placeholder ready) |
| securityContext | `privileged: true` (TODO: reduce to specific capabilities) |
| Service Account | `openshell-supervisor` (dedicated, privileged SCC on OCP) |

---

## openshell_claude

**Type:** Builtin sandbox | **CLI:** Claude Code | **LLM:** Anthropic API | **Supervisor:** Yes

### Overview
Pre-installed Claude Code CLI in the OpenShell base sandbox image. Created
via Sandbox CR, managed by the OpenShell gateway. Supports native
`.claude/skills/` for kagenti skill execution.

### Files
```
# No custom files — uses upstream base image
# Sandbox CRD: agents.x-k8s.io/v1alpha1
# Image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest (~1.1GB)
```

### Deployment
```bash
# Create sandbox via Sandbox CR
kubectl apply -f - <<EOF
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: claude-sandbox
  namespace: team1
spec:
  podTemplate:
    spec:
      containers:
      - name: sandbox
        image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest
        command: ["sleep", "3600"]
        volumeMounts:
        - name: workspace
          mountPath: /workspace
      volumes:
      - name: workspace
        persistentVolumeClaim:
          claimName: claude-workspace-pvc
EOF
```

### Testing
```bash
kubectl exec <sandbox-pod> -n team1 -- which claude
kubectl exec <sandbox-pod> -n team1 -- claude --version
```

### LLM Configuration
Requires gateway provider configuration (Phase 2):
```bash
kubectl set env statefulset/openshell-gateway -n openshell-system \
  ANTHROPIC_API_KEY=<real-anthropic-key>
```

### Sandboxing Features
All features active via the supervisor in the base image.

---

## openshell_opencode

**Type:** Builtin sandbox | **CLI:** OpenCode | **LLM:** OpenAI-compatible | **Supervisor:** Yes

### Overview
Pre-installed OpenCode CLI in the OpenShell base sandbox image. Works with
LiteMaaS via OpenAI-compatible format. Created via Sandbox CR.

### Files
Same as `openshell_claude` — uses the same base image.

### Deployment
Same as `openshell_claude` — create a Sandbox CR.

### LLM Configuration
Works with LiteMaaS (OpenAI-compatible):
```bash
kubectl set env statefulset/openshell-gateway -n openshell-system \
  OPENAI_API_KEY=<litemass-key> \
  OPENAI_BASE_URL=https://litellm-prod.apps.maas.redhatworkshops.io/v1
```

### Testing
```bash
kubectl exec <sandbox-pod> -n team1 -- which opencode
kubectl exec <sandbox-pod> -n team1 -- opencode --version
```

### Sandboxing Features
Same as `openshell_claude` — all features active via supervisor.

---

## openshell_generic

**Type:** Builtin sandbox | **CLI:** None | **LLM:** N/A | **Supervisor:** Yes

### Overview
Generic sandbox with no pre-installed agent CLI. Used for workspace
persistence testing and as a template for custom sandbox configurations.

### Deployment
```bash
kubectl apply -f - <<EOF
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: generic-sandbox
  namespace: team1
spec:
  podTemplate:
    spec:
      containers:
      - name: sandbox
        image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest
        command: ["sleep", "3600"]
EOF
```

### Sandboxing Features
All supervisor features active but no agent to use them.
