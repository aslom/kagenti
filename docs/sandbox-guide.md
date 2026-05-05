# Kagenti Sandbox Guide (OpenShell)

This guide covers installing and using the Kagenti sandboxing feature powered by
[OpenShell](https://github.com/NVIDIA/OpenShell). Sandboxes provide kernel-level
isolation for autonomous AI agents with credential protection and network policy
enforcement.

## Prerequisites

- `kubectl` (or `oc` for OpenShift) configured for your cluster
- Helm 3.x
- Docker (for Kind) or access to an OpenShift 4.16+ cluster
- An LLM API key (e.g., Anthropic)

## Install the OpenShell CLI

Download the latest release for your platform from
<https://github.com/kagenti/OpenShell/releases>:


Verify the installation:

```bash
openshell --version
```

## Platform Installation

Choose the section that matches your target environment.

---

### Kind (Local Development)

#### Step 1: Deploy Kagenti on Kind

```bash
scripts/kind/setup-kagenti.sh  --with-all
```

This deploys: Kind cluster, Istio ambient mesh, cert-manager, Keycloak, SPIRE,
and the Kagenti platform.

#### Step 2: Deploy OpenShell Shared Infrastructure

```bash
scripts/openshell/deploy-shared.sh
```

This creates:

- Sandbox controller CRDs
- Gateway API experimental CRDs (TLSRoute support)
- cert-manager CA chain (`ClusterIssuer: openshell-ca`)
- Keycloak realm `openshell` with PKCE client, roles, users, and groups

#### Step 3: Deploy Tenant Gateways

Each tenant gets an isolated namespace, gateway, and TLS certificates:

```bash
# Deploy one or both tenants
scripts/openshell/deploy-tenant.sh team1
scripts/openshell/deploy-tenant.sh team2
```

Each deployment creates an OpenShell gateway StatefulSet (gateway +
compute-driver + credentials-driver), mTLS certificates, RBAC, and an Istio
TLSRoute for external access.

Tenant endpoints on Kind:

| Tenant | Endpoint |
|--------|----------|
| team1  | `https://openshell-team1.localtest.me:30443` |
| team2  | `https://openshell-team2.localtest.me:30443` |

---

### OpenShift

For OpenShift clusters, use the Kagenti OpenShift installer which includes
OpenShell as part of the platform deployment.

Refer to [docs/ocp/openshift-install.md](ocp/openshift-install.md) for full
installation instructions including SPIRE setup (ZTWIM operator on OCP 4.19+,
Helm charts on 4.16–4.18).

The OpenShift installer handles:

- OpenShell CRDs and shared infrastructure
- Tenant gateway deployment with OpenShift Routes (instead of Kind NodePorts)
- cert-manager integration with cluster CA
- Keycloak realm and client configuration

After installation, continue with [Configure the CLI](#configure-the-cli) below.

---

## Configure the CLI

Point the CLI at your tenant gateway:

```bash
# Kind
scripts/openshell/configure-cli.sh team1

# OpenShift — use the route hostname for your tenant
openshell gateway add \
  --name team1 \
  --url https://openshell-team1.<cluster-domain> \
  --ca-cert /path/to/ca.crt \
  --client-cert /path/to/tls.crt \
  --client-key /path/to/tls.key
```

On Kind, the `configure-cli.sh` script extracts certificates from the cluster
automatically. On OpenShift, retrieve the certificates from the
`openshell-client-cert` secret in the tenant namespace.

## Log In

```bash
openshell gateway login
```

This opens a browser for Keycloak OIDC PKCE authentication. Use one of the
preconfigured users:

| User  | Password | Teams        | Role  |
|-------|----------|--------------|-------|
| alice | alice123 | team1        | admin |
| bob   | bob123   | team2        | admin |
| admin | admin123 | team1, team2 | admin |

## Create a Provider

Providers define how the sandbox connects to an LLM. You must be logged in as an
admin user.

```bash
export ANTHROPIC_AUTH_TOKEN="your-api-key-here"

openshell provider create --name claude --type anthropic \
  --credential ANTHROPIC_AUTH_TOKEN \
  --config ANTHROPIC_BASE_URL=https://your-litellm-proxy.example.com
```

Key points:

- `--type` must be `anthropic`, `openai`, or `nvidia` (these are the supported
  inference routing types)
- API keys go in `--credential` (managed by SecretResolver, never exposed to the
  sandbox)
- Base URLs go in `--config` (used for inference route resolution only)

## Configure Inference Routing

```bash
openshell inference set --provider claude --model claude-sonnet-4-6 --no-verify
```

This tells the gateway how to route `inference.local` requests from inside the
sandbox to the upstream LLM endpoint.

## Create a Sandbox

```bash
openshell sandbox create --provider claude --no-auto-providers -- claude
```

Flags:

| Flag | Purpose |
|------|---------|
| `--provider claude` | Bind the named provider to this sandbox |
| `--no-auto-providers` | Don't auto-create providers from local env vars |
| `-- claude` | Command to run inside the sandbox (Claude Code) |

The sandbox pod starts with:

- Network isolation (only `inference.local` allowed outbound)
- Credential protection (API keys resolved at the proxy layer, never in the
  sandbox env)
- Kernel-level enforcement (Landlock, seccomp)

## Connect to an Existing Sandbox

```bash
# Interactive session
openshell sandbox connect

# Run a one-off command
openshell sandbox exec -n <sandbox-name> -- claude --print "Hello"
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `invalid peer certificate: UnknownIssuer` | CLI missing CA/client certs | Re-run `configure-cli.sh` or place `ca.crt`, `tls.crt`, `tls.key` in `~/.config/openshell/gateways/<name>/mtls/` |
| `POST openshell:80/... not permitted by policy` | URL placed in `--credential` instead of `--config` | Recreate provider with URL in `--config` |
| `Failed to connect to api.anthropic.com` | CLI auto-created a provider with wrong type | Use `--no-auto-providers` flag |
| `/v1/v1/messages` double path | `ANTHROPIC_BASE_URL` includes a trailing `/v1` | Remove `/v1` suffix — the SDK appends its own |
| `context_management: Extra inputs not permitted` | LiteLLM rejects experimental beta parameters | Set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` in the provider config |
| `connection not allowed by policy` | Inference bundle not loaded | Run `openshell inference get` and verify route count |

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ openshell   │────▶│ OpenShell        │────▶│ Keycloak         │
│ CLI         │     │ Gateway          │     │ (OIDC PKCE)      │
└─────────────┘     └──────────────────┘     └──────────────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
           ┌──────────────┐  ┌───────────────────┐
           │ Compute      │  │ Sandbox Pod        │
           │ Driver       │  │ ┌───────────────┐  │
           │ (creates pod)│  │ │ Supervisor    │  │
           └──────────────┘  │ │ + HTTP Proxy  │  │
                             │ └───────┬───────┘  │
                             │         │          │
                             │         ▼          │
                             │ ┌───────────────┐  │
                             │ │ Claude Code   │  │
                             │ │ (sandboxed)   │  │
                             │ └───────────────┘  │
                             └───────────────────┘
                                       │
                                       ▼
                             ┌───────────────────┐
                             │ LLM Upstream      │
                             │ (via inference    │
                             │  proxy routing)   │
                             └───────────────────┘
```

The sandbox never sees raw API keys. Credentials are resolved at the proxy layer
inside the supervisor, which intercepts requests to `inference.local` and injects
the real API key before forwarding to the upstream LLM.
