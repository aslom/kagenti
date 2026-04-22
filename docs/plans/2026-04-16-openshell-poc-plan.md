# OpenShell PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy upstream OpenShell + Kagenti Operator on Kind with Weather, Google ADK, and Claude SDK agents, verified by E2E tests.

**Architecture:** OpenShell Gateway + K8s Compute Driver in `openshell-system`, Kagenti Operator in `kagenti-system`, three supervised agents in `team1`. E2E tests call agents via A2A.

**Tech Stack:** Helm, kubectl, Python (pytest, httpx), OpenShell upstream images, Google ADK SDK, Anthropic Claude SDK

---

### Task 1: OpenShell Deployment Manifests

**Files:**
- Create: `deployments/openshell/namespace.yaml`
- Create: `deployments/openshell/gateway.yaml`
- Create: `deployments/openshell/rbac.yaml`
- Create: `deployments/openshell/kustomization.yaml`

- [ ] **Step 1:** Create `openshell-system` namespace manifest
- [ ] **Step 2:** Create Gateway + K8s Compute Driver deployment from upstream Helm chart values or raw manifests (use `ghcr.io/nvidia/openshell/gateway:latest` image)
- [ ] **Step 3:** Create RBAC (ClusterRole for Sandbox CRD management, ServiceAccount)
- [ ] **Step 4:** Create kustomization.yaml tying it together
- [ ] **Step 5:** Test: `kubectl apply -k deployments/openshell/` on Kind, verify pods Running
- [ ] **Step 6:** Commit

### Task 2: Env File + Fulltest Script

**Files:**
- Create: `deployments/envs/dev_values_openshell.yaml`
- Create: `.github/scripts/local-setup/openshell-full-test.sh`

- [ ] **Step 1:** Create env file enabling: cert-manager, Istio, Keycloak, SPIRE, LiteLLM, PostgreSQL, Budget Proxy, Kagenti Operator. Disabling: UI, Backend, Shipwright.
- [ ] **Step 2:** Create fulltest script: cluster create → deps → operator → OpenShell → agents → tests
- [ ] **Step 3:** Test: run `openshell-full-test.sh --skip-cluster-destroy` on Kind
- [ ] **Step 4:** Commit

### Task 3: Weather Agent (Supervised)

**Files:**
- Create: `deployments/openshell/agents/weather-agent.yaml`
- Create: `deployments/openshell/agents/weather-policy.yaml`

- [ ] **Step 1:** Create weather agent Deployment with OpenShell supervisor as entrypoint, connecting to gateway
- [ ] **Step 2:** Create OPA policy ConfigMap (allow weather API endpoints)
- [ ] **Step 3:** Create AgentRuntime CR for weather agent
- [ ] **Step 4:** Deploy and verify pod Running with supervisor process tree
- [ ] **Step 5:** Test A2A call manually: `curl -X POST http://weather-agent.team1.svc:8080/ -d '{"jsonrpc":"2.0","method":"message/send",...}'`
- [ ] **Step 6:** Commit

### Task 4: Google ADK Agent

**Files:**
- Create: `deployments/openshell/agents/adk-agent/`
- Create: `deployments/openshell/agents/adk-agent/agent.py`
- Create: `deployments/openshell/agents/adk-agent/Dockerfile`
- Create: `deployments/openshell/agents/adk-agent/deployment.yaml`
- Create: `deployments/openshell/agents/adk-agent/policy.yaml`

- [ ] **Step 1:** Create minimal ADK agent with PR review skill using google-adk SDK
- [ ] **Step 2:** Create Dockerfile extending supervisor base image
- [ ] **Step 3:** Create deployment manifest (supervised, LLM via Budget Proxy)
- [ ] **Step 4:** Build image, deploy, verify pod Running
- [ ] **Step 5:** Test A2A call manually
- [ ] **Step 6:** Commit

### Task 5: Claude SDK Agent

**Files:**
- Create: `deployments/openshell/agents/claude-sdk-agent/`
- Create: `deployments/openshell/agents/claude-sdk-agent/agent.py`
- Create: `deployments/openshell/agents/claude-sdk-agent/Dockerfile`
- Create: `deployments/openshell/agents/claude-sdk-agent/deployment.yaml`
- Create: `deployments/openshell/agents/claude-sdk-agent/policy.yaml`

- [ ] **Step 1:** Create minimal Claude SDK agent with code review skill using anthropic SDK
- [ ] **Step 2:** Create Dockerfile extending supervisor base image
- [ ] **Step 3:** Create deployment manifest (supervised, LLM via Budget Proxy)
- [ ] **Step 4:** Build image, deploy, verify pod Running
- [ ] **Step 5:** Test A2A call manually
- [ ] **Step 6:** Commit

### Task 6: E2E Tests

**Files:**
- Create: `kagenti/tests/e2e/openshell/conftest.py`
- Create: `kagenti/tests/e2e/openshell/test_platform_health.py`
- Create: `kagenti/tests/e2e/openshell/test_weather_agent.py`
- Create: `kagenti/tests/e2e/openshell/test_adk_agent.py`
- Create: `kagenti/tests/e2e/openshell/test_claude_sdk_agent.py`
- Create: `kagenti/tests/e2e/openshell/test_sandbox_lifecycle.py`
- Create: `kagenti/tests/e2e/openshell/test_credential_isolation.py`

- [ ] **Step 1:** Create conftest with fixtures (A2A client, gateway client, namespace config)
- [ ] **Step 2:** Create platform health tests (all pods Running, gateway responsive)
- [ ] **Step 3:** Create weather agent tests (conversation + multi-city)
- [ ] **Step 4:** Create ADK agent tests (conversation + PR review skill)
- [ ] **Step 5:** Create Claude SDK agent tests (conversation + code review)
- [ ] **Step 6:** Create sandbox lifecycle tests (create/list/delete via gateway)
- [ ] **Step 7:** Create credential isolation tests (placeholder env vars, supervisor process tree)
- [ ] **Step 8:** Run all tests on Kind: `uv run pytest kagenti/tests/e2e/openshell/ -v`
- [ ] **Step 9:** Commit

### Task 7: CI Pipelines

**Files:**
- Create: `.github/workflows/e2e-openshell-kind.yaml`
- Create: `.github/workflows/e2e-openshell-hypershift.yaml`

- [ ] **Step 1:** Create Kind workflow (push to `feat/openshell-*`, non-voting)
- [ ] **Step 2:** Create HyperShift workflow (`/run-e2e-openshell`, non-voting)
- [ ] **Step 3:** Commit and push, verify workflow appears in Actions tab
- [ ] **Step 4:** Update draft PR with all implementation
