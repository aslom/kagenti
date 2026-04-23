# OpenShell Sandboxing Layers

> Back to [main doc](openshell-integration.md)

## Supervisor as Container Entrypoint

Each agent pod uses the OpenShell supervisor as the container entrypoint:

1. Supervisor starts (`ENTRYPOINT`)
2. Connects to OpenShell Gateway via `OPENSHELL_GATEWAY` env var
3. Reads OPA/Rego policy
4. Applies Landlock (filesystem restrictions) + custom seccomp (syscall filtering)
5. Drops all capabilities
6. Execs the agent process as a restricted child

The agent inherits kernel-enforced isolation for its entire lifetime. Normal pod
networking is preserved (no network namespace in PoC), so Istio mesh works unchanged.

### Protection layers

| Layer | Mechanism | Locked? | Reloadable? |
|-------|-----------|---------|-------------|
| **Filesystem** | Linux Landlock LSM — kernel-level path allowlist | At sandbox creation | No |
| **Network** | HTTP CONNECT proxy (forced via veth/netns) + OPA/Rego | At sandbox creation | Yes (hot-reload) |
| **Process** | Seccomp BPF — syscall allowlist | At sandbox creation | No |
| **Inference** | Credential stripping + backend injection + model ID rewriting | At sandbox creation | Yes (hot-reload) |

### Credential isolation

OpenShell implements zero-secret credential isolation. Agent env vars contain
**placeholder tokens** (`openshell:resolve:env:API_KEY`), not real secrets. The
supervisor proxy resolves placeholders to real credentials at the HTTP layer
via TLS termination before forwarding upstream.

For LLM calls, the supervisor's inference router strips agent-supplied auth
headers entirely and injects backend API keys from the gateway's credential store.

### Egress policy enforcement

| Agent | Supervisor? | OPA Enforced? | Egress |
|-------|------------|---------------|--------|
| weather-agent | No | No | **Open** (plain K8s pod) |
| weather-agent-supervised | **Yes** | **Yes** | Restricted to `*.svc.cluster.local` + LiteMaaS |
| adk-agent | No | No (policy mounted but not enforced) | **Open** |
| claude-sdk-agent | No | No (policy mounted but not enforced) | **Open** |

Non-supervised agents have OPA policy files mounted at `/etc/openshell/policy.yaml`
as preparation for supervisor integration. The policies are NOT enforced until the
supervisor binary is the container entrypoint.

**Blocker for full enforcement:** The supervisor creates a network namespace
that blocks `kubectl port-forward` and K8s readiness probes. Solutions:
1. Upstream: supervisor exposes agent port through the proxy
2. Workaround: run tests from inside the cluster (test runner pod)
3. Workaround: sidecar that bridges the netns port to the pod network

## Security: Init Container Pattern (TODO)

The PoC uses `privileged: true` on the supervised agent container because
the supervisor needs `CAP_SYS_ADMIN` + `CAP_NET_ADMIN` for network namespace
creation.

**Minimum capability set** (from codebase research):

| Capability | Required for | Can be dropped? |
|------------|-------------|-----------------|
| `CAP_NET_ADMIN` | veth pairs, netns, IPs, routes | No |
| `CAP_SYS_ADMIN` | `unshare()`, `setns()`, Landlock ABI | No |
| `CAP_SYS_PTRACE` | OPA proxy process inspection | Possibly |

**Target (production):** Use an **init container** for the supervisor:

```yaml
initContainers:
- name: supervisor-init
  image: ghcr.io/nvidia/openshell/supervisor:latest
  securityContext:
    privileged: true   # Only init container is privileged
  command: ["/usr/local/bin/openshell-sandbox", "--setup-only"]

containers:
- name: agent
  image: agent:latest
  securityContext:
    allowPrivilegeEscalation: false
    capabilities:
      drop: [ALL]      # Agent has zero capabilities
```

**Requires:** Upstream OpenShell support for `--setup-only` mode.

## OpenShell RFC 0001

OpenShell is being rearchitected via [RFC 0001](https://github.com/NVIDIA/OpenShell/pull/836)
into a composable, driver-based system with four pluggable subsystems:

| Subsystem | Purpose | Kagenti Mapping |
|-----------|---------|-----------------|
| **Compute** | Sandbox lifecycle (K8s, Podman, VM) | Kagenti as compute driver (Phase 2) |
| **Credentials** | Secret resolution (Vault, K8s Secrets) | Delivers secrets to supervisor proxy |
| **Control-plane identity** | User/operator auth (mTLS, OIDC) | Keycloak OIDC |
| **Sandbox identity** | Workload identity (SPIFFE) | SPIRE |

## LLM Compatibility Matrix

| Agent / CLI | LiteMaaS (llama-scout, deepseek) | Anthropic API | OpenAI API |
|-------------|----------------------------------|---------------|------------|
| **Claude CLI** (base image) | **No** — validates model name | **Yes** (native) | No |
| **Claude SDK agent** (custom) | **Yes** — OpenAI-compatible format | Yes (native SDK) | Yes |
| **ADK agent** (Google ADK) | **Yes** — via LiteLLM wrapper | N/A | Yes |
| **OpenCode** (base image) | **Yes** — OpenAI-compatible | N/A | Yes |
| **Codex** (base image) | Partial — may need real OpenAI key | N/A | Yes |
| **Copilot** (base image) | No — proprietary GitHub API | N/A | N/A |

**Key limitation:** Claude CLI requires a real Anthropic API key. Our custom
Claude SDK agent works with LiteMaaS because it uses httpx with the OpenAI
chat/completions format, bypassing Claude CLI's model validation.
