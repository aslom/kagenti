# Architecture Alignment Review — OpenShell + Kagenti

**Date:** 2026-04-25
**Author:** Ladislav Smola
**For review by:** Paolo Dettori
**Status:** Working document — action items at the end
**Prior doc:** [2026-04-23 Proposal Alignment](2026-04-23-openshell-proposal-alignment.md)

**Source links:**
- [Issue #1155](https://github.com/kagenti/kagenti/issues/1155) — epic: Adopt agent-sandbox as fourth workload type
- [PR #1300](https://github.com/kagenti/kagenti/pull/1300) — OpenShell PoC code + tests (draft)
- [PR #1307](https://github.com/kagenti/kagenti/pull/1307) — CI workflows for Kind + HyperShift
- [PR #1318](https://github.com/kagenti/kagenti/pull/1318) — Agent-sandbox router integration (merged)
- [PR #1319](https://github.com/kagenti/kagenti/pull/1319) — Architecture documentation (open)
- [Paolo's setup.sh](https://github.com/pdettori/kagenti/blob/openshell-phase0-kind-setup/scripts/kind/openshell/setup.sh) — Phase 0 Kind setup
- [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) — upstream CRD project
- [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell) — sandbox runtime

---

## 1. Issue #1155 Review: Adopt agent-sandbox as Fourth Workload Type

[Issue #1155](https://github.com/kagenti/kagenti/issues/1155) defines a 9-task
Phase 1 plan to adopt the `agents.x-k8s.io/v1alpha1` Sandbox CRD as a fourth
workload type (alongside Deployment, StatefulSet, Job).

### What's Already Done

**[PR #1318](https://github.com/kagenti/kagenti/pull/1318) (merged)** implements task 1.4 (Router integration):
- `create_agent`: Sandbox branch guarded by `kagenti_feature_flag_agent_sandbox`
- `list_agents`: Fourth query for Sandbox CRs
- `get_agent`: Fourth try for Sandbox
- `delete_agent`: Sandbox deletion in cleanup sequence

**Our PoC ([PR #1300](https://github.com/kagenti/kagenti/pull/1300))** already implements significant portions:
- CRD installed on both Kind and HyperShift
- Sandbox CR lifecycle tested in [`test_04_sandbox_lifecycle.py`](../../kagenti/tests/e2e/openshell/test_04_sandbox_lifecycle.py) (list, create, delete)
- Gateway reconciles Sandbox CRs into pods
- PVC workspace persistence tested in [`test_10_workspace_persistence.py`](../../kagenti/tests/e2e/openshell/test_10_workspace_persistence.py)

### Task-by-Task Review with Questions

| Task | Status | PoC Coverage | Questions |
|------|--------|-------------|-----------|
| 1.1 Feature flag | Not started | Our PoC uses `kagenti_feature_flag_sandbox` (existing) | Should the new flag gate OpenShell-specific behavior too, or is `agent_sandbox` purely for the CRD workload type? |
| 1.2 KubernetesService CRUD | Not started | Our tests do raw `kubectl` CRUD | Should the backend CRUD use the gateway gRPC API (which manages Sandbox CRs) or direct K8s API? |
| 1.3 Manifest builder | Not started | Our `deployments/openshell/` has static YAML | How should `_build_sandbox_manifest()` handle the supervisor binary delivery? Init container (Paolo's recommendation) or bake-into-image (current PoC)? |
| 1.4 Router integration | **Done (PR #1318)** | Merged | — |
| 1.5 Reconciliation | Not started | Not tested | Does reconciliation need to distinguish OpenShell-managed Sandboxes from plain agent-sandbox CRs? |
| 1.6 Operator compatibility | Not tested | PoC deploys gateway separately | Does `AgentCard.spec.targetRef` work with Sandbox kind? Our PoC doesn't use AgentCard CRs for OpenShell agents. |
| 1.7 CRD detection | Not started | Our fulltest checks CRD availability | Should detection happen at startup (issue's proposal) or lazily on first use? |
| 1.8 UI changes | Not started | PoC doesn't modify Kagenti UI | Should "Sandbox" workload type show OpenShell-specific fields (provider, policy, supervisor status)? |
| 1.9 E2E tests | **Partially done** | 117 test items (92 functions, parametrized), 10 categories | Our tests are OpenShell-specific. Issue wants generic agent-sandbox tests. Can our test suite serve as the reference? |

### Key Architecture Question for Issue #1155

**Who manages Sandbox CRs — Kagenti backend directly, or OpenShell gateway?**

The issue assumes the Kagenti backend creates Sandbox CRs directly via K8s API
(same pattern as Deployment/StatefulSet/Job). But in our PoC, the OpenShell
gateway is the Sandbox lifecycle manager — it watches CRs, creates pods,
manages supervisor injection, handles provider credentials.

Two models:

| Model | Backend creates | Gateway manages |
|-------|----------------|----------------|
| **A: Direct** | Backend → K8s API → Sandbox CR → agent-sandbox-controller → Pod | No gateway needed for basic sandboxes |
| **B: Via Gateway** | Backend → Gateway gRPC → Gateway → Sandbox CR → Pod | Gateway adds supervisor, credentials, policy, SSH |

**Recommendation:** Model B for OpenShell sandboxes (security layers require gateway),
Model A for plain agent-sandbox CRs (no supervisor needed). The feature flag
controls which path: `agent_sandbox` = Model A, `sandbox` + OpenShell = Model B.

### Questions to Post on Issue #1155

1. **Task 1.2 — CRUD path:** PR #1318 adds Sandbox to the router, but should the
   backend call `custom_api` directly (as the issue describes) or delegate to the
   OpenShell gateway gRPC for sandboxes that need supervisor/provider injection?

2. **Task 1.3 — Manifest builder:** The OpenShell gateway generates pod specs from
   Sandbox CRs with supervisor init containers, provider env vars, and OPA policy
   mounts. Should `_build_sandbox_manifest()` replicate this logic, or should it
   create minimal Sandbox CRs and let the gateway handle pod spec generation?

3. **Task 1.6 — Operator compatibility:** Our PoC deploys agents via static YAML
   in `deployments/openshell/agents/`, not via `AgentCard` CRs. When the operator
   reconciles an AgentCard with `targetRef: {kind: Sandbox}`, does it need to be
   aware that the sandbox pod may have been created by the OpenShell gateway
   (with supervisor, netns, OPA) rather than the agent-sandbox-controller alone?

4. **Task 1.9 — Test scope:** Our PoC has 117 E2E tests across 10 categories
   (platform health, A2A connectivity, credential security, supervisor enforcement,
   skill execution, etc.). Should the issue's E2E tests (task 1.9) cover just
   basic CRUD, or should they include security verification (Landlock, seccomp,
   netns, OPA) which requires the OpenShell supervisor?

5. **Phase 2 alignment — SandboxTemplate vs OpenShell provider:** Issue #1155 Phase 2
   introduces `SandboxTemplate` / `SandboxClaim`. OpenShell has a similar concept:
   `openshell provider create` defines credential bundles, and policies define
   security constraints. Should `SandboxTemplate` wrap OpenShell providers, or
   are they parallel concepts?

---

## 2. Paolo's setup.sh vs Our openshell-full-test.sh — Overlap Analysis

### Script Comparison

| Feature | Paolo's `setup.sh` | Our `openshell-full-test.sh` |
|---------|-------------------|------------------------------|
| **Location** | [`scripts/kind/openshell/setup.sh`](https://github.com/pdettori/kagenti/blob/openshell-phase0-kind-setup/scripts/kind/openshell/setup.sh) | [`.github/scripts/local-setup/openshell-full-test.sh`](../../.github/scripts/local-setup/openshell-full-test.sh) |
| **Gateway install** | Helm chart ([`deploy/helm/openshell`](https://github.com/NVIDIA/OpenShell/tree/main/deploy/helm/openshell)) | Kustomize (`kubectl apply -k` [`deployments/openshell/`](../../deployments/openshell/)) |
| **Namespace** | `openshell` | `openshell-system` |
| **TLS** | mTLS enabled, Helm auto-generates certs | `--disable-tls --disable-gateway-auth` |
| **Ingress** | Istio Gateway + TLSRoute (NodePort 30443→host 9443) | None (port-forward in tests) |
| **Supervisor** | hostPath on Kind node (`/opt/openshell/bin/`) | Baked into agent image (multi-stage Docker) |
| **CLI config** | Extracts mTLS certs, configures `~/.config/openshell/` | No CLI config |
| **Smoke test** | `openshell sandbox create/exec/delete` | pytest E2E suite |
| **CRD source** | [OpenShell repo](https://github.com/NVIDIA/OpenShell/tree/main/deploy/kube/manifests) (`deploy/kube/manifests/agent-sandbox.yaml`) | Our repo ([`deployments/openshell/`](../../deployments/openshell/)) |
| **Custom agents** | None | 4 agents (weather, ADK, Claude SDK, supervised) |
| **LiteLLM** | None | Inline deployment with 7 model aliases |
| **Platform** | Kind only | Kind + HyperShift/OCP |
| **Prerequisite** | Kagenti Kind cluster ([`scripts/kind/setup-kagenti.sh`](https://github.com/kagenti/kagenti/blob/main/scripts/kind/setup-kagenti.sh)) | Inline Kagenti install (Phase 2) |
| **Teardown** | `--teardown` flag | Phase 6 cluster destroy |
| **Idempotent** | Yes (Helm upgrade --install) | Yes (kubectl apply) |

### Key Differences

1. **Helm vs Kustomize:** Paolo uses the upstream Helm chart with cert auto-generation.
   We use raw YAML manifests applied via kustomize. Helm is the upstream-recommended
   path and supports version pinning, values overrides, and rolling upgrades.

2. **TLS enabled vs disabled:** Paolo's gateway has mTLS enabled (production-like).
   Ours disables TLS for simplicity in PoC. For the CLI path (`openshell sandbox create`),
   TLS must be enabled because the CLI authenticates via mTLS client certs.

3. **Ingress:** Paolo sets up Istio Gateway API + TLSRoute for external access.
   We don't expose the gateway externally. This is needed for the CLI experience.

4. **Supervisor delivery:** Paolo copies the binary to the Kind node via `docker cp`
   (hostPath). We bake it into the agent image (multi-stage build). Paolo's approach
   matches the proposal's Option A (hostPath) while the proposal recommends Option C
   (init container + emptyDir).

5. **Namespace:** Paolo uses `openshell`, we use `openshell-system`. Needs alignment.

### Proposed Shared deploy-openshell.sh

Extract the common deployment logic into a shared function library that both
scripts can source:

```
.github/scripts/
├── kind/
│   ├── create-cluster.sh         ← existing Kind cluster creation
│   ├── deploy-platform.sh        ← existing Kagenti installer (Kind)
│   └── ...
├── local-setup/
│   ├── openshell-full-test.sh    ← our fulltest (cluster + agents + tests)
│   └── openshell-build-agents.sh ← OCP binary builds / Kind docker loads
scripts/
├── kind/
│   └── openshell/
│       └── setup.sh              ← NEW: Paolo's Phase 0 setup (Helm, TLS, CLI)
├── lib/
│   └── deploy-openshell.sh       ← NEW: shared functions
└── ocp/
    └── setup-kagenti.sh          ← existing OCP installer
```

**`scripts/lib/deploy-openshell.sh`** would provide:

```bash
# Shared functions sourced by both scripts
deploy_openshell_crd()        # Install AgentSandbox CRD + controller
deploy_openshell_gateway()    # Helm install gateway (configurable TLS, namespace)
deploy_openshell_litellm()    # Deploy LiteLLM model proxy with aliases
deploy_openshell_agents()     # Deploy custom A2A agents
configure_openshell_cli()     # Extract mTLS certs, configure CLI
setup_openshell_ingress()     # Gateway API + TLSRoute (Kind) or Route (OCP)
prepull_sandbox_image()       # Pre-pull base sandbox image
```

**Usage from each script:**

```bash
# Paolo's setup.sh
source "$(dirname "$0")/../../lib/deploy-openshell.sh"
deploy_openshell_crd
deploy_openshell_gateway --helm --tls
setup_openshell_ingress --kind
configure_openshell_cli

# Our openshell-full-test.sh
source "$REPO_ROOT/scripts/lib/deploy-openshell.sh"
deploy_openshell_crd
deploy_openshell_gateway --helm --tls
deploy_openshell_litellm
deploy_openshell_agents
# Then run E2E tests
```

### Migration Path

1. **Phase A (now):** Keep both scripts as-is. Document the overlap in this doc.
2. **Phase B (next PR):** Extract shared functions into `deploy-openshell.sh`.
   Both scripts source it. Paolo's setup.sh becomes a thin wrapper that calls
   shared functions + adds CLI config + ingress.
3. **Phase C (production):** Single Helm chart for the full OpenShell stack
   (gateway + CRD + supervisor + ingress + LiteLLM). Scripts become
   `helm upgrade --install openshell charts/openshell/ -f values-kind.yaml`.

---

## 3. Route / TLSRoute Setup — CLI Access Path

The CLI experience (`openshell sandbox create -- claude`) requires the gateway
to be accessible from outside the cluster. This is the ingress strategy.

### Kind (Istio Gateway API + TLSRoute)

From Paolo's setup.sh — validated on Kind:

```
Client (openshell CLI)
  │ mTLS (client cert)
  ▼
host:9443
  │ Docker port mapping
  ▼
NodePort 30443
  │
  ▼
Istio Gateway (TLS Passthrough)
  │ L4 passthrough (no TLS termination)
  ▼
TLSRoute → openshell:8080
  │
  ▼
OpenShell Gateway StatefulSet
  │ gRPC/mTLS
  ▼
Sandbox Pod (SSH bridge)
```

**Requirements:**
- Experimental Gateway API CRDs installed (`v1.4.0`)
- `PILOT_ENABLE_ALPHA_GATEWAY_API=true` on istiod
- Kind extraPortMappings: `containerPort: 30443, hostPort: 9443`
- Gateway resource with `protocol: TLS, tls.mode: Passthrough`
- TLSRoute pointing to `openshell:8080`

**Current gap:** Our Kind cluster config (`.github/scripts/kind/create-cluster.sh`
on main, or the inline Kind config in `openshell-full-test.sh`) doesn't map
port 30443/9443. This needs to be added.

### OCP (Passthrough Route)

```
Client (openshell CLI)
  │ mTLS (client cert)
  ▼
openshell.apps.<cluster-domain>:443
  │ OpenShift Router
  ▼
Passthrough Route (TLS passthrough, no termination)
  │
  ▼
openshell:8080
  │
  ▼
OpenShell Gateway StatefulSet
```

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: openshell
  namespace: openshell-system
spec:
  host: openshell.apps.<cluster-domain>
  port:
    targetPort: 8080
  tls:
    termination: passthrough
  to:
    kind: Service
    name: openshell
```

**Current gap:** No Route created in our OCP deployment. Needs to be added
to `deploy_openshell_gateway()` for OCP platform.

### Action Items

1. Add `containerPort: 30443, hostPort: 9443` to Kind cluster config
2. Add TLSRoute creation to shared deploy function (Kind path)
3. Add Passthrough Route creation to shared deploy function (OCP path)
4. Enable TLS on gateway (switch from `--disable-tls` to Helm with cert auto-gen)
5. Add CLI configuration step to shared deploy function

---

## 4. OpenShell Provider Mechanism — Zero-Trust Credential Delivery

Paolo shared the production workflow for using Claude Code with IBM LiteLLM
through the OpenShell provider mechanism. This is the **key discovery** for
our credential delivery architecture.

### Paolo's Validated Workflow

```bash
# Step 1: Create a named provider with credentials
openshell provider create --name litellm \
   --type generic \
   --credential "ANTHROPIC_AUTH_TOKEN=<your-token>"

# Step 2: Define network egress policy
cat > policy.yaml << 'EOF'
version: 1
network_policies:
  ibm_litellm:
    name: IBM LiteLLM
    endpoints:
      - host: ete-litellm.ai-models.vpc-int.res.ibm.com
        port: 443
        access: full
    binaries:
      - path: /usr/local/bin/claude
      - path: /usr/bin/curl
      - path: /usr/bin/node
      - path: /usr/local/bin/node
EOF

# Step 3: Create sandbox with provider + policy
openshell sandbox create --name kagenti \
  --provider litellm \
  --policy policy.yaml \
  --no-auto-providers

# Step 4: Inside sandbox, configure LLM endpoint
export ANTHROPIC_BASE_URL="https://ete-litellm.ai-models.vpc-int.res.ibm.com"
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
claude
```

### How This Works (Architecture)

```
openshell provider create
  │ Stores credential in gateway DB (encrypted)
  ▼
openshell sandbox create --provider litellm
  │ Gateway creates Sandbox CR
  │ Supervisor starts in pod
  │ Supervisor calls gateway gRPC: GetProviderEnvironment("litellm")
  │ Gateway returns: ANTHROPIC_AUTH_TOKEN=<decrypted-token>
  │ Supervisor injects env var into agent process
  ▼
Agent (Claude Code) sees ANTHROPIC_AUTH_TOKEN
  │ But can ONLY reach endpoints in policy.yaml
  │ OPA proxy blocks all other egress
  ▼
Agent calls ANTHROPIC_BASE_URL (LiteLLM)
  │ LiteLLM validates token, routes to real LLM
  ▼
Response flows back through OPA proxy → agent
```

### What This Means for Our PoC

This is the **zero-trust credential delivery** we've been designing around.
The agent never sees the real API key in its environment at startup — the
supervisor injects it after authenticating with the gateway.

**Current PoC approach (K8s Secrets):**
```yaml
env:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: litellm-virtual-keys
        key: api-key
```

**OpenShell provider approach (zero-trust)** — adapted from Paolo's workflow
for our in-cluster LiteLLM (using `OPENAI_API_KEY` instead of `ANTHROPIC_AUTH_TOKEN`
since our agents use the OpenAI-compatible API):
```bash
openshell provider create --name kagenti-litellm \
  --type generic \
  --credential "OPENAI_API_KEY=$(kubectl get secret litellm-virtual-keys -n team1 -o jsonpath='{.data.api-key}' | base64 -d)"
```

The provider approach is **strictly better** because:
1. Credentials are stored encrypted in the gateway DB, not in K8s Secrets
2. The supervisor injects them at runtime, not at pod creation
3. OPA policy restricts which endpoints the credential can reach
4. The agent process cannot exfiltrate the key (network namespace blocks it)

### Adaptation for Our Kind/HyperShift Setup

For our LiteLLM proxy (in-cluster), the provider + policy would be:

```bash
# Provider: LiteLLM virtual key
openshell provider create --name kagenti-litellm \
  --type generic \
  --credential "OPENAI_API_KEY=sk-kagenti-team1-key"

# Policy: Allow only in-cluster LiteLLM
cat > policy-litellm.yaml << 'EOF'
version: 1
network_policies:
  kagenti_litellm:
    name: Kagenti LiteLLM Proxy
    endpoints:
      - host: litellm-model-proxy.team1.svc.cluster.local
        port: 4000
        access: full
    binaries:
      - path: /usr/local/bin/claude
      - path: /usr/bin/node
      - path: /usr/local/bin/node
      - path: /usr/local/bin/opencode
EOF

# Create sandbox
openshell sandbox create --name test-claude \
  --provider kagenti-litellm \
  --policy policy-litellm.yaml \
  --no-auto-providers
```

### Prerequisite: TLS + Ingress Must Be Enabled

The `openshell provider create` and `openshell sandbox create` commands
require the CLI to connect to the gateway via mTLS. This means:

1. Gateway must have TLS enabled (not `--disable-tls`)
2. Ingress must be configured (TLSRoute on Kind, Route on OCP)
3. CLI must be configured with mTLS certs

This is why Section 3 (Route/TLSRoute setup) is a prerequisite for this
workflow. Without ingress, the CLI can't reach the gateway.

---

## 5. Testing Skill-Running Agents on Kind

### Quick Start — Run a Skill on a Deployed Agent

Our E2E tests validate skill execution on ADK and Claude SDK agents. Here's
how to run them manually on a Kind cluster with LiteLLM.

#### Prerequisites

```bash
# 1. Deploy the full stack (creates Kind cluster, installs Kagenti, deploys
#    OpenShell gateway, agents, and LiteLLM model proxy)
./.github/scripts/local-setup/openshell-full-test.sh --skip-cluster-destroy

# 2. Verify agents are running
kubectl get pods -n team1 | grep -E 'weather|adk|claude-sdk'

# 3. Verify LiteLLM is running
kubectl get pods -n team1 | grep litellm

# 4. Check LLM connectivity (should return a model list)
kubectl exec -n team1 deploy/litellm-model-proxy -- \
  curl -s http://localhost:4000/v1/models | head -5
```

#### Run a PR Review Skill (ADK Agent)

> **A2A format note:** Parts use `"type": "text"` per the A2A spec and our
> [`conftest.py:191`](../../kagenti/tests/e2e/openshell/conftest.py) helper.

```bash
# Port-forward to ADK agent
kubectl port-forward -n team1 svc/adk-agent 8001:8000 &

# Send a PR review request via A2A protocol
curl -s -X POST http://localhost:8001/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Review this PR diff for security issues:\n\n```diff\n--- a/app.py\n+++ b/app.py\n@@ -1,5 +1,8 @@\n import os\n+import subprocess\n \n def run_command(cmd):\n-    return os.popen(cmd).read()\n+    user_input = input(\"Enter command: \")\n+    result = subprocess.run(user_input, shell=True, capture_output=True)\n+    return result.stdout.decode()\n```"}]
      }
    }
  }' | python3 -m json.tool

# Kill port-forward
kill %1
```

#### Run an RCA Skill (Claude SDK Agent)

```bash
# Port-forward to Claude SDK agent
kubectl port-forward -n team1 svc/claude-sdk-agent 8002:8000 &

# Send an RCA analysis request
curl -s -X POST http://localhost:8002/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Perform root cause analysis on this CI failure:\n\nJob: e2e-tests\nStep: Run pytest\nError: FAILED test_auth.py::test_login_redirect\nTraceback:\n  File \"test_auth.py\", line 45\n    assert response.status_code == 302\nAssertionError: 200 != 302\n\nRecent changes:\n- Updated auth middleware to check JWT expiry\n- Added CORS headers to all responses"}]
      }
    }
  }' | python3 -m json.tool

kill %1
```

#### Run a Security Review Skill (Claude SDK Agent)

```bash
kubectl port-forward -n team1 svc/claude-sdk-agent 8002:8000 &

curl -s -X POST http://localhost:8002/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Security review this Kubernetes deployment:\n\n```yaml\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web-app\nspec:\n  template:\n    spec:\n      containers:\n      - name: app\n        image: myapp:latest\n        securityContext:\n          privileged: true\n        env:\n        - name: DB_PASSWORD\n          value: \"supersecret123\"\n        ports:\n        - containerPort: 8080\n```"}]
      }
    }
  }' | python3 -m json.tool

kill %1
```

#### Run All Skill Tests via pytest

Source: [`test_07_skill_execution.py`](../../kagenti/tests/e2e/openshell/test_07_skill_execution.py)

```bash
# Run only A2A agent skill tests (excludes openshell sandbox variants)
export OPENSHELL_LLM_AVAILABLE=true
uv run pytest kagenti/tests/e2e/openshell/test_07_skill_execution.py -v \
  --timeout=300 -k "not openshell" 2>&1 | tail -30

# Expected results:
#   test_create_skills_configmap — PASS
#   test_skills_configmap_has_index — PASS
#   test_skills_include_review — PASS
#   test_weather_agent_lists_skills — PASS
#   test_claude_sdk_has_code_review_skill — PASS
#   test_pr_review__adk_agent — PASS (LLM)
#   test_pr_review__claude_sdk_agent — PASS (LLM)
#   test_rca__claude_sdk_agent — PASS (LLM)
#   test_security_review__claude_sdk_agent — PASS (LLM)
#   test_review_real_github_pr — PASS (LLM + GitHub API)
#   test_rca_style_log_analysis — PASS (LLM)
```

### Using OpenShell Providers for Skill Testing (Paolo's Approach)

Once TLS + ingress are enabled (Section 3), you can test skills through
OpenShell-managed sandboxes with provider credential injection:

```bash
# 1. Create provider with LiteLLM key
openshell provider create --name kagenti-litellm \
  --type generic \
  --credential "ANTHROPIC_AUTH_TOKEN=$(kubectl get secret litellm-virtual-keys \
    -n team1 -o jsonpath='{.data.api-key}' | base64 -d)"

# 2. Create policy allowing LiteLLM endpoint
cat > /tmp/policy-litellm.yaml << 'EOF'
version: 1
network_policies:
  kagenti_litellm:
    name: Kagenti LiteLLM
    endpoints:
      - host: litellm-model-proxy.team1.svc.cluster.local
        port: 4000
        access: full
    binaries:
      - path: /usr/local/bin/claude
      - path: /usr/bin/node
      - path: /usr/local/bin/node
EOF

# 3. Create sandbox with Claude Code
openshell sandbox create --name skill-test \
  --provider kagenti-litellm \
  --policy /tmp/policy-litellm.yaml \
  --no-auto-providers

# 4. Inside sandbox, configure and run
export ANTHROPIC_BASE_URL="http://litellm-model-proxy.team1.svc.cluster.local:4000"
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1

# Test PR review skill:
claude --print "Review this PR diff for security issues: ..."

# Test RCA skill:
claude --print "Perform root cause analysis on this CI log: ..."
```

### Using Your Vertex Model on Kind

To use your Vertex-compatible model instead of LiteMaaS, update the LiteLLM
config to point to your endpoint:

```bash
# 1. Update the LiteLLM ConfigMap with your Vertex model
kubectl edit configmap litellm-config -n team1

# Change the model_list entries to point to your endpoint:
# model_list:
#   - model_name: gpt-4o-mini
#     litellm_params:
#       model: vertex_ai/gemini-pro
#       vertex_project: your-project
#       vertex_location: us-central1

# 2. Restart LiteLLM
kubectl rollout restart deploy/litellm-model-proxy -n team1

# 3. Verify model is available
kubectl exec -n team1 deploy/litellm-model-proxy -- \
  curl -s http://localhost:4000/v1/models

# 4. Run skill tests (same as above — agents route through LiteLLM)
export OPENSHELL_LLM_AVAILABLE=true
uv run pytest kagenti/tests/e2e/openshell/test_07_skill_execution.py -v --timeout=300
```

For the OpenShell provider path (Claude Code in sandbox):

```bash
# Create provider pointing to your Vertex endpoint
openshell provider create --name vertex \
  --type generic \
  --credential "ANTHROPIC_AUTH_TOKEN=<your-vertex-api-key>"

# Policy allowing your Vertex endpoint
cat > /tmp/policy-vertex.yaml << 'EOF'
version: 1
network_policies:
  vertex:
    name: Vertex AI
    endpoints:
      - host: <your-vertex-endpoint>
        port: 443
        access: full
    binaries:
      - path: /usr/local/bin/claude
      - path: /usr/bin/node
      - path: /usr/local/bin/node
EOF

openshell sandbox create --name vertex-test \
  --provider vertex \
  --policy /tmp/policy-vertex.yaml \
  --no-auto-providers
```

---

## 6. Architecture Alignment Matrix

### What Aligns

| Area | PoC | Proposal/Issue | Notes |
|------|-----|---------------|-------|
| Sandbox CRD | Installed, tested | Core of #1155 | Same CRD, same controller |
| Gateway deployment | StatefulSet in openshell-system | StatefulSet via Helm | Converging on Helm |
| Supervisor security | Landlock + seccomp + netns + OPA verified | Full security stack | 12 enforcement tests |
| Agent-sandbox controller | External prerequisite | External prerequisite (issue #1155) | Aligned on deployment model |
| Feature flag pattern | `kagenti_feature_flag_sandbox` | `kagenti_feature_flag_agent_sandbox` (new) | Two flags for two concerns |
| E2E testing in Kind | 83 passed / 0 failed / 34 skipped | Task 1.9 in #1155 | Our suite exceeds #1155 scope |

### What Diverges (Needs Alignment)

| Area | PoC | Proposal/Issue | Action Needed |
|------|-----|---------------|---------------|
| Gateway install method | Kustomize (raw YAML) | Helm chart | Switch to Helm |
| TLS | Disabled | Enabled with auto-gen certs | Enable TLS |
| Namespace | `openshell-system` | `openshell` | Align on `openshell` |
| Ingress | None | TLSRoute (Kind), Route (OCP) | Add ingress setup |
| Supervisor delivery | Baked into image | hostPath (Paolo) / init container (proposal) | Switch to init container |
| CLI configuration | None | mTLS cert extraction | Add CLI config step |
| Credential delivery | K8s secretKeyRef | OpenShell provider mechanism | Migrate to providers |
| Backend CRUD path | Not implemented | Direct K8s API (#1155) | Decide: direct vs gateway |

### What's Complementary (No Conflict)

| PoC Provides | Proposal/Issue Provides |
|-------------|------------------------|
| 4 custom A2A agents | CLI sandbox experience |
| LiteLLM model proxy with aliases | Provider credential injection |
| 117 test items across 10 categories | Smoke test (create/exec/delete) |
| HyperShift/OCP deployment | Kind-focused Phase 0 |
| Skill discovery + execution | No skill concept |
| Multi-framework agent testing | Single-agent (Claude) focus |
| Kagenti UI integration path | CLI-only interaction model |

---

## 7. Current Test Results

| Platform | Passed | Failed | Skipped | Total |
|----------|--------|--------|---------|-------|
| Kind | 83 | 0 | 34 | 117 |
| HyperShift | 79 | 0 | 38 | 117 |

> **Note:** 117 = pytest-collected test items (92 test functions × parametrize
> expansions across agent types). See [e2e-test-matrix.md](../../docs/agentic-runtime/e2e-test-matrix.md)
> for the full breakdown.

**Key fix applied:** TCP readiness probes for LiteLLM (Istio ambient mTLS
compatible). This was the root cause of all LLM test failures — HTTP probes
fail through ztunnel because kubelet doesn't use mTLS.

---

## 8. Action Items

### Immediate (This PR Cycle)

1. **Fix markdown validation errors** on [PR #1319](https://github.com/kagenti/kagenti/pull/1319) (Paolo's review comment)
2. **Enable TLS on gateway** — switch from `--disable-tls` to Helm with cert auto-gen
3. **Align namespace** to `openshell` (matching Paolo's setup.sh)
4. **Add Kind port mapping** for 30443→9443 to enable CLI access

### Next PR

5. **Create `scripts/lib/deploy-openshell.sh`** with shared functions
6. **Add TLSRoute setup** (Kind) and Passthrough Route (OCP)
7. **Add CLI configuration** step (extract mTLS certs)
8. **Switch supervisor delivery** to init container + emptyDir

### Phase 2

9. **Migrate credential delivery** to OpenShell provider mechanism
10. **Decide Backend CRUD path** for Sandbox CRs (direct K8s API vs gateway gRPC)
11. **Post questions on issue #1155** (Section 1 above)
12. **Add `SandboxTemplate` alignment** to Phase 2 planning

---

## Appendix A: Paolo's setup.sh Key Design Decisions

These are worth preserving as reference for the shared deploy function:

1. **Auto-clone OpenShell repo** if not found locally — searches `../openshell`,
   `~/openshell`, then clones from GitHub. Good for CI.

2. **Helm chart TLS patch** — creates `tls-secrets.yaml` template that auto-generates
   CA + server + client certs using Helm's `genCA`/`genSignedCert`. Certs are only
   generated if the Secret doesn't already exist (idempotent).

3. **Gateway API experimental CRDs** — installs `v1.4.0` experimental CRDs for
   TLSRoute support. Required because TLSRoute is still alpha.

4. **istiod env patch** — sets `PILOT_ENABLE_ALPHA_GATEWAY_API=true` to enable
   Istio's TLS passthrough support.

5. **NodePort pinning** — patches the Istio-created Service to use NodePort 30443
   (mapped to host 9443 via Kind extraPortMappings).

6. **CLI gateway config** — writes `~/.config/openshell/gateways/<context>/metadata.json`
   with endpoint and cert paths. Sets active gateway.

7. **Smoke test** — creates sandbox, waits for Ready, execs `echo "openshell-ok"`,
   deletes. Validates full lifecycle in ~2 minutes.

## Appendix B: Issue #1155 Phase 2 Preview

Phase 2 introduces three new concepts that affect our architecture:

| Concept | What It Does | Impact on PoC |
|---------|-------------|---------------|
| **SandboxTemplate** | Admin defines reusable pod templates with security defaults | Could wrap OpenShell providers + policies |
| **SandboxClaim** | User requests sandbox from template (like PVC from StorageClass) | Maps to Kagenti UI sandbox wizard |
| **SandboxWarmPool** | Pre-warmed pods for instant allocation | Eliminates cold start for interactive sandboxes |

These are Phase 2 scope — not blocking current work, but should influence
our architecture decisions (e.g., don't build custom template systems that
will be superseded by SandboxTemplate).
