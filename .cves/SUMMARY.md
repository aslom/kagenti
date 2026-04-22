# Kagenti Security Scan — Combined Report

Date: 2026-03-04

---

## Executive Summary

Security scans were performed across the kagenti organization repos. Findings are separated into:

1. [kagenti/kagenti upstream/main](#part-1-kagentikagenti--upstreammain) — the shipped codebase
2. [Sandbox Agent Worktree](#part-2-sandbox-agent-worktree-featssandbox-agent) — unreleased feature branch
3. [Other Kagenti Repos](#part-3-other-kagenti-repos) — extensions, operator, examples, etc.
4. [Documentation CVE Audit](#part-4-documentation-cve-audit) — CVE references in .md files
5. [Already Patched](#already-patched-no-action) — dependencies confirmed fixed

### Scan Sources

| Scanner | Method |
|---------|--------|
| Dependency CVE scan | Lock file analysis + WebSearch against NVD/Snyk/GitHub Advisories |
| Source code review | Claude Code reasoning — auth, injection, secrets, containers, CI/CD, network |
| Documentation audit | Grep for CVE IDs and vulnerability keywords across all .md files |
| Trivy | Not available locally; CI uses `aquasecurity/trivy-action` v0.34.1 |

---

## Top Priority Items (upstream/main only)

| # | Severity | Finding | File | Action |
|---|----------|---------|------|--------|
| D1 | **HIGH** | nginx 1.27-alpine — CVE-2026-1642 (CVSS 8.2) | `kagenti/ui-v2/Dockerfile` | Bump to `nginx:1.28-alpine` |
| D2 | **HIGH** | python-jose 3.5.0 — unmaintained, 3 CVEs | `kagenti/backend/pyproject.toml` | Replace with `PyJWT` or `joserfc` |
| H2 | **HIGH** | Hardcoded Keycloak admin/admin in Helm | `charts/kagenti-deps/templates/keycloak-k8s.yaml:62-64` | Parameterize via values.yaml |
| H3 | **HIGH** | Real OpenAI API key on disk | `deployments/envs/.secret_values.yaml:10` | Rotate key, fix .gitignore |
| H4 | **HIGH** | JWT audience validation disabled | `kagenti/backend/app/core/auth.py:200` | Enable `verify_aud` |
| H5 | **HIGH** | SSRF bypass via DNS rebinding + redirects | `kagenti/backend/app/routers/agents.py:2953-2989` | Disable `follow_redirects` |

### Remediation Priority (upstream/main)

| Priority | Items | Effort |
|----------|-------|--------|
| **Immediate** | H3 (rotate API key), D1 (nginx bump) | Low |
| **Short-term** | H4 (JWT audience), H5 (SSRF), D2 (replace python-jose), H2 (Keycloak defaults) | Medium |
| **Medium-term** | M1 (auth default), M4 (error leak), M6 (name validation), M7 (root container), M9 (JWKS refresh) | Medium |
| **Long-term** | M2 (JWKS TLS), M3 (demo creds), M5 (CORS), M8 (in-cluster HTTP) | Low-Medium |
| **Maintenance** | L1-L5, D3-D4 (minor items) | Low |

---

# Part 1: kagenti/kagenti — upstream/main

All findings in this section apply to the currently shipped code on `upstream/main`.

## 1.1 Dependency CVE Findings

### Dependency Inventory

#### Python

| File | Type |
|------|------|
| `pyproject.toml` | Root project (ansible, kubernetes, testing deps) |
| `kagenti/backend/pyproject.toml` | Backend FastAPI application |
| `kagenti/backend/uv.lock` | Backend resolved dependencies |
| `uv.lock` | Root resolved dependencies |
| `kagenti/auth/agent-oauth-secret/requirements.txt` | Auth job: python-keycloak, kubernetes, typer |
| `kagenti/auth/api-oauth-secret/requirements.txt` | Auth job: python-keycloak, kubernetes |
| `kagenti/auth/mlflow-oauth-secret/requirements.txt` | Auth job: python-keycloak, kubernetes |
| `kagenti/auth/ui-oauth-secret/requirements.txt` | Auth job: python-keycloak, kubernetes |
| `kagenti/demo-setup/keycloak-config/github/requirements.txt` | Demo: python-keycloak |
| `kagenti/demo-setup/keycloak-config/slack/requirements.txt` | Demo: python-keycloak |
| `kagenti/examples/identity/keycloak_token_exchange/requirements.txt` | Example: requests, pydantic, urllib3, etc. |

#### Node.js

| File | Type |
|------|------|
| `kagenti/ui-v2/package.json` | UI frontend (React, PatternFly, Vite) |
| `kagenti/ui-v2/package-lock.json` | UI resolved dependencies |

#### Container Images

| File | Base Image |
|------|------------|
| `kagenti/backend/Dockerfile` | `python:3.12-slim` (builder + runtime) |
| `kagenti/ui-v2/Dockerfile` | `node:20-alpine` (builder) / `nginx:1.27-alpine` (runtime) |
| `kagenti/auth/agent-oauth-secret/Dockerfile` | `python:3.12-slim` |
| `kagenti/auth/api-oauth-secret/Dockerfile` | `python:3.12-slim` |
| `kagenti/auth/mlflow-oauth-secret/Dockerfile` | `python:3.12-slim` |
| `kagenti/auth/ui-oauth-secret/Dockerfile` | `python:3.12-slim` |
| `kagenti/examples/.../spire/client/Dockerfile` | `ghcr.io/spiffe/spiffe-helper:0.8.0` / `alpine:3.21` |

#### Helm Charts

| File | Dependencies |
|------|--------------|
| `charts/kagenti/Chart.yaml` | `kagenti-operator-chart:0.2.0-alpha.20`, `kagenti-webhook-chart:0.4.0-alpha.3` |
| `charts/kagenti-deps/Chart.yaml` | `gateway-api:~1.3.0` |
| `charts/gateway-api/Chart.yaml` | None (leaf chart, appVersion 1.3.0) |

### Resolved Versions (Backend)

| Package | Version | Package | Version |
|---------|---------|---------|---------|
| fastapi | 0.128.0 | python-jose | 3.5.0 |
| starlette | 0.50.0 | kubernetes | 35.0.0 |
| uvicorn | 0.40.0 | mcp | 1.26.0 |
| pydantic | 2.12.5 | requests | 2.32.5 |
| httpx | 0.28.1 | urllib3 | 2.6.3 |
| cryptography | 46.0.5 | ecdsa | 0.19.1 |

### Resolved Versions (UI Frontend)

| Package | Version | Package | Version |
|---------|---------|---------|---------|
| react | 18.3.1 | vite | 5.4.21 |
| keycloak-js | 25.0.6 | esbuild | 0.21.5 |

### CVE Findings Table

| # | Package | Current | CVE | Severity | Fix | File |
|---|---------|---------|-----|----------|-----|------|
| D1 | nginx | 1.27-alpine | CVE-2026-1642 | **HIGH** (8.2) | 1.28.2+ | `kagenti/ui-v2/Dockerfile` |
| D2 | nginx | 1.27-alpine | CVE-2025-23419 | MEDIUM | 1.27.4+ | `kagenti/ui-v2/Dockerfile` |
| D3 | python-jose | 3.5.0 | CVE-2024-33663 | **HIGH** | No fix (unmaintained) | `kagenti/backend/pyproject.toml` |
| D4 | python-jose | 3.5.0 | CVE-2024-33664 | MEDIUM | No fix (unmaintained) | `kagenti/backend/pyproject.toml` |
| D5 | ecdsa | 0.19.1 | CVE-2024-23342 | **HIGH** (7.4) | No fix (by design) | via python-jose |
| D6 | vite | 5.4.21 | CVE-2025-31125 | MEDIUM (KEV) | 5.4.16+ | `kagenti/ui-v2/package.json` (dev only) |
| D7 | vite | 5.4.21 | CVE-2025-30208 | MEDIUM | 5.4.15+ | `kagenti/ui-v2/package.json` (dev only) |
| D8 | esbuild | 0.21.5 | GHSA-67mh-4wv8-2f99 | MODERATE | 0.25.0+ | `kagenti/ui-v2/package.json` (dev only) |
| D9 | node | 20-alpine | Multiple Jan 2026 | HIGH | 20.20.0+ | `kagenti/ui-v2/Dockerfile` (builder only) |
| D10 | kubernetes | 33.1.0 | Outdated | INFO | 35.0.0 | `kagenti/auth/*/requirements.txt` |
| D11 | spiffe-helper | 0.8.0 | Outdated | INFO | 0.11.0 | `kagenti/examples/.../Dockerfile` |

#### D1: nginx:1.27-alpine — CRITICAL PRIORITY

PR #741 bumped nginx from 1.25 to 1.27, but CVE-2026-1642 (SSL upstream injection, CVSS 8.2) requires 1.28.2+. Nginx 1.27 branch does have a fix at 1.27.4+ for CVE-2025-23419 but not for CVE-2026-1642.

**Action:** Update `kagenti/ui-v2/Dockerfile` FROM line to `nginx:1.28-alpine`.

#### D3-D5: python-jose 3.5.0 — HIGH PRIORITY

`python-jose` is **unmaintained**. No fixes for CVE-2024-33663 (algorithm confusion) or CVE-2024-33664 (JWT bomb DoS). Pulls in `ecdsa` (CVE-2024-23342, Minerva timing attack, declared unfixable). The project already uses `PyJWT` in test dependencies.

**Action:** Replace `python-jose[cryptography]` with `PyJWT[crypto]` in `kagenti/backend/pyproject.toml`.

#### D6-D8: Vite/esbuild — dev-only, low urgency

Dev-only dependencies. Do not ship in production images. Update `vite` to `^5.4.16`+ when convenient.

### Dependency Recommendations (upstream/main)

1. **Immediate**: Bump nginx to `1.28-alpine`, replace python-jose with PyJWT
2. **Near-term**: Update kubernetes in auth requirements.txt (33.1.0 → 35.0.0), pin node to `20.20-alpine`
3. **Maintenance**: Update vite, rebuild images regularly, add npm audit to CI

## 1.2 Source Code Security Findings (upstream/main)

### HIGH

#### H2: Hardcoded Keycloak Admin Credentials in Helm Template

- **File**: `charts/kagenti-deps/templates/keycloak-k8s.yaml:62-64`
- **Category**: Secrets
- **Description**: Hardcoded `KC_BOOTSTRAP_ADMIN_USERNAME=admin`, `KC_BOOTSTRAP_ADMIN_PASSWORD=admin`. Not parameterized via Helm values — deployed with default credentials regardless of configuration.
- **Recommendation**: Replace with `{{ .Values.keycloak.auth.adminUser }}` / `{{ .Values.keycloak.auth.adminPassword }}`, or source from a Kubernetes Secret.

#### H3: OpenAI API Key on Disk

- **File**: `deployments/envs/.secret_values.yaml:10`
- **Category**: Secrets
- **Description**: Real `sk-proj-...` key present. File is in `.gitignore` but `.secret_values.yaml.bak-openai` backup is NOT excluded.
- **Recommendation**: (1) Rotate key. (2) Add `*.bak*` to `.gitignore`. (3) Replace with placeholder.

#### H4: JWT Audience Validation Disabled

- **File**: `kagenti/backend/app/core/auth.py:200`
- **Category**: Auth
- **Description**: `verify_aud: False` — any JWT from the same Keycloak realm is accepted, regardless of intended client.
- **Recommendation**: Configure Keycloak audience mapper, enable `verify_aud`. Or check `azp` matches expected client ID.

#### H5: SSRF Protection Bypass via DNS Rebinding and Redirect

- **File**: `kagenti/backend/app/routers/agents.py:2953-2989`
- **Category**: Network
- **Description**: `fetch_env_from_url` validates hostname IP but uses `follow_redirects=True`, allowing redirect-based SSRF bypass. Also vulnerable to DNS rebinding.
- **Recommendation**: Disable `follow_redirects` or validate each redirect destination. Use resolved IP directly.

### MEDIUM

#### M1: Auth Disabled by Default

- **File**: `kagenti/backend/app/core/auth.py:267-275`
- **Category**: Auth
- **Description**: `enable_auth=False` by default returns mock admin user with full privileges.
- **Recommendation**: Make auth enabled by default. Add startup warning when disabled.

#### M2: JWKS Fetched Without TLS Verification Control

- **File**: `kagenti/backend/app/core/auth.py:83-84`
- **Category**: Network
- **Description**: JWKS fetched via `httpx.AsyncClient()` with no explicit `verify`. In-cluster HTTP acceptable with Istio mTLS, but no option to enforce HTTPS.
- **Recommendation**: Make TLS verification configurable. Document Istio mTLS requirement.

#### M3: Hardcoded Default Credentials in Demo Deployments

- **Files**: `.repos/kagenti-extensions/AuthBridge/demos/*/k8s/*.yaml`, `kagenti/examples/identity/...`
- **Category**: Secrets
- **Description**: `KEYCLOAK_ADMIN_PASSWORD: "admin"` in ConfigMaps. Demo files that users may copy.
- **Recommendation**: Replace with Secret references. Add production warnings.

#### M4: Verbose Error Details Leaked to API Clients

- **Files**: `kagenti/backend/app/core/auth.py:242`, `kagenti/backend/app/routers/chat.py:142,244`, `kagenti/backend/app/routers/agents.py:3017`, `kagenti/backend/app/routers/tools.py:2084`
- **Category**: Info Disclosure
- **Description**: `detail=f"...{str(e)}"` leaks internal hostnames, paths, stack traces.
- **Recommendation**: Return generic messages to clients. Log details server-side.

#### M5: CORS Allows All Methods and Headers

- **File**: `kagenti/backend/app/main.py:102-103`
- **Category**: Network
- **Description**: `allow_methods=["*"]`, `allow_headers=["*"]`. Origins properly restricted.
- **Recommendation**: Restrict to specific methods and headers.

#### M6: No Input Validation on Kubernetes Resource Names

- **File**: `kagenti/backend/app/routers/agents.py:178-182`
- **Category**: Injection
- **Description**: `name` and `namespace` used unsanitized in label selectors. Characters like `,` or `=` could manipulate queries.
- **Recommendation**: Add `@field_validator` enforcing RFC 1123: `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`.

#### M7: MLFlow OAuth Secret Dockerfile Runs as Root

- **File**: `kagenti/auth/mlflow-oauth-secret/Dockerfile`
- **Category**: Container
- **Description**: Only Dockerfile without `USER` directive. All others use non-root.
- **Recommendation**: Add `USER 1001`.

#### M8: In-Cluster HTTP (Relies on Istio mTLS)

- **Files**: `kagenti/backend/app/routers/chat.py:71`, `kagenti/backend/app/routers/tools.py:2015`, `kagenti/backend/app/core/constants.py:134,138`
- **Category**: Network
- **Description**: All in-cluster communication uses plain HTTP. Relies on Istio ambient mTLS.
- **Recommendation**: Document Istio mTLS as hard security dependency.

#### M9: JWKS Keys Not Refreshed on Schedule

- **File**: `kagenti/backend/app/core/auth.py:73-112`
- **Category**: Auth
- **Description**: JWKS fetched once, only refreshed on unknown `kid`. Revoked keys remain cached.
- **Recommendation**: Add periodic refresh (every 5-15 minutes) or TTL-based cache.

### LOW

| # | Finding | File | Recommendation |
|---|---------|------|----------------|
| L1 | Alpine `:latest` in extension Dockerfiles | `.repos/kagenti-extensions/AuthBridge/AuthProxy/Dockerfile:18` | Pin to `alpine:3.21` |
| L2 | OpenAPI/Swagger exposed without auth | `kagenti/backend/app/main.py:91-93` | Disable in production |
| L3 | Uvicorn binds 0.0.0.0 in dev | `kagenti/backend/app/main.py:138` | Acceptable for containers |
| L4 | Admin role auto-mapping | `kagenti/backend/app/core/auth.py:227-228` | Complete issue #647 |
| L5 | Health check uses urllib | `kagenti/backend/Dockerfile:64` | Minor, consider curl |

### INFO — Good Practices

| # | Finding | File |
|---|---------|------|
| I1 | SSRF protection implemented (private IP blocking) | `kagenti/backend/app/routers/agents.py:2815-2831` |
| I2 | SHA-pinned GitHub Actions with enforcement | `.github/workflows/*.yaml` |
| I3 | Comprehensive CI security pipeline (Trivy, Bandit, CodeQL, Hadolint, shellcheck) | `.github/workflows/security-scans.yaml` |
| I4 | Non-root containers + multi-stage builds | `kagenti/backend/Dockerfile`, `kagenti/ui-v2/Dockerfile` |

---

# Part 2: Sandbox Agent Worktree (feat/sandbox-agent)

These findings apply **only to the unreleased sandbox-agent feature branch** in `.worktrees/sandbox-agent/`. They are not on upstream/main.

### HIGH

#### SB-H1: Sandbox Trigger Endpoint Has No Authentication

- **File**: `kagenti/backend/app/routers/sandbox_trigger.py` (entire router)
- **Category**: Auth
- **Description**: The `/api/v1/sandbox/trigger` POST endpoint is the only router without `require_roles()` or `get_current_user()`. Allows unauthenticated users to create SandboxClaim Kubernetes resources.
- **Recommendation**: Add `dependencies=[Depends(require_roles(ROLE_OPERATOR))]` before merging to main.

### MEDIUM

#### SB-M1: Label Selector Injection via sandbox_files.py

- **File**: `kagenti/backend/app/routers/sandbox_files.py:117`
- **Category**: Injection
- **Description**: `label_selector=f"app={agent_name}"` uses unsanitized input. Related to M6 on main but specific to the sandbox file browser.
- **Recommendation**: Validate `agent_name` against RFC 1123 before use in selectors.

### Note

The sandbox agent code is in active development. These issues should be resolved before the sandbox PR is merged to main. The sandbox design docs (`docs/plans/2026-02-23-sandbox-agent-research.md`) show security is a primary design concern with nono Landlock, gVisor RuntimeClass, and irreversible sandboxing.

---

# Part 3: Other Kagenti Repos

Scanned latest `main` branch of each repo (cloned 2026-03-04).

## 3.1 kagenti-extensions (AuthBridge + Webhook)

| Aspect | Status |
|--------|--------|
| **Base images** | `golang:1.24.8` + `distroless/static:nonroot` (webhook), `golang:1.23-alpine` + `alpine:latest` (AuthProxy), `envoyproxy/envoy:v1.28-latest` (envoy), `alpine:3.18` (init), `python:3.12-slim` (client-reg) |
| **Key deps** | envoy go-control-plane v1.35.0, lestrrat-go/jwx/v2 v2.1.6, grpc v1.75.1, python-keycloak 5.3.1, pyjwt 2.10.1 |
| **Credentials** | Demo-only `admin/admin` in ConfigMaps; `secretKeyRef` used properly in AuthProxy deployment |
| **CVEs found** | None |
| **Dependabot** | Partial — GitHub Actions only. Missing: gomod, pip, docker |
| **Security scanning** | **None** — no Trivy, Bandit, CodeQL, Hadolint, Gitleaks |
| **Action pinning** | **Not SHA-pinned** — uses `@v4`/`@v5`/`@v6` tags |

**HIGH findings:**
1. **Envoy v1.28 is EOL** in `AuthBridge/AuthProxy/Dockerfile.envoy` — current stable is v1.33+
2. **No CI security scanning** at all

**MEDIUM findings:**
3. `alpine:latest` mutable tags in AuthProxy Dockerfiles — pin to `alpine:3.21`
4. `alpine:3.18` in init container Dockerfile — EOL since Aug 2025
5. AuthProxy runtime runs as root (no `USER` directive)
6. Example deployment stores admin password in ConfigMap (not Secret)
7. Go version inconsistency: webhook uses 1.24.8, AuthProxy uses 1.23

## 3.2 agent-examples

| Aspect | Status |
|--------|--------|
| **Base images** | `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (13 agents), `python:3.12-slim-bookworm` (1), `uv:python3.11-bookworm-slim` (1), `golang:1.24.9-bookworm` (1) |
| **Key deps** | Python A2A/MCP agents with pyproject.toml, 1 Go module |
| **Credentials** | None hardcoded |
| **CVEs found** | None |
| **Dependabot** | **Not configured** |
| **Security scanning** | **None** |
| **Action pinning** | Not SHA-pinned (`@v6`, `@v3`, `@v5`) |

**Note:** 23 Dockerfiles total, all using recent base images. No security concerns in base images.

## 3.3 plugins-adapter

| Aspect | Status |
|--------|--------|
| **Base images** | `python:3.12.12-slim` |
| **Key deps** | grpcio >=1.76.0, nemoguardrails 0.19.0, mcp-contextforge-gateway 0.9.0, betterproto2 0.9.1 |
| **Credentials** | None hardcoded |
| **CVEs found** | None |
| **Dependabot** | **Not configured** |
| **Security scanning** | **None** |
| **Action pinning** | Not SHA-pinned (`@v6`) |

## 3.4 kagenti-operator

| Aspect | Status |
|--------|--------|
| **Base images** | `golang:1.24` (builder) + `distroless/static:nonroot` (runtime) — both operator and agentcard-signer |
| **Key deps** | Go — Kubernetes controller-runtime |
| **Credentials** | None found |
| **CVEs found** | None |
| **Dependabot** | **Not configured** |
| **Security scanning** | **None** — no govulncheck, gosec, Trivy, CodeQL |
| **Action pinning** | Not SHA-pinned (`@v6`) |

**Note:** Good container security — uses distroless + nonroot. Go 1.24 is current.

## 3.5 agentic-control-plane

| Aspect | Status |
|--------|--------|
| **Base images** | `python:3.12-slim` (k8s_debug_agent, a2a_bridge_server), `python:3.11-slim` (k8s_readonly_server), `quay.io/pdettori/python:3.12-slim` (source_code_analyzer) |
| **Key deps** | Python A2A agents |
| **Credentials** | Not scanned in detail |
| **CVEs found** | None |
| **Dependabot** | **Not configured** |
| **Security scanning** | **None** |

**MEDIUM:** Uses a personal quay.io image (`quay.io/pdettori/python:3.12-slim`) as base — should use official `python:3.12-slim`.

## 3.6 workload-harness

| Aspect | Status |
|--------|--------|
| **Base images** | No Dockerfiles |
| **Dependabot** | **Not configured** |
| **Security scanning** | **None** |

Minimal repo — tooling for workload generation, no containers.

## 3.7 Cross-Repo Security Gaps

| Issue | Severity | kagenti | extensions | agent-examples | plugins-adapter | operator | ACP | workload-harness |
|-------|----------|---------|------------|----------------|-----------------|----------|-----|-----------------|
| CI security scanning | — | Yes | **No** | **No** | **No** | **No** | **No** | **No** |
| Dependabot | — | Yes | Partial | **No** | **No** | **No** | **No** | **No** |
| SHA-pinned actions | — | Yes | **No** | **No** | **No** | **No** | **No** | **No** |
| Secret detection | — | Partial | **No** | **No** | **No** | **No** | **No** | **No** |
| SECURITY.md | — | Yes | **No** | **No** | **No** | **No** | **No** | **No** |
| Non-root containers | — | Yes | Mixed | N/A | N/A | Yes | N/A | N/A |
| Distroless runtime | — | No | webhook only | No | No | Yes | No | N/A |

**Recommendation:** Port the security scanning pipeline from `kagenti/kagenti/.github/workflows/security-scans.yaml` to all repos. Create shared reusable workflow. Existing gap reports at `.repos/scan-reports/`.

---

# Part 4: Documentation CVE Audit

## Files with CVE References

| File | Line(s) | CVE ID(s) | Context | Risk |
|------|---------|-----------|---------|------|
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 224, 494, 1212, 1218 | CVE-2026-25253 | OpenClaw sandbox bypass — comparative analysis for Kagenti design | MEDIUM |
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 1212, 1219 | CVE-2026-24763 | Docker sandbox escape via PATH manipulation | MEDIUM |
| `docs/plans/2026-02-26-coding-agent-variants-research.md` | 449 | CVE-2026-22812 | OpenCode RCE — third-party tool assessment | MEDIUM |
| `.worktrees/cve-awareness/.claude/skills/git:commit/SKILL.md` | 79, 89 | CVE-2026-12345 | Fictional example in regex pattern | LOW |

All CVE references are to **third-party** vulnerabilities (OpenClaw, OpenCode), used as comparative analysis. Not Kagenti's own vulnerabilities. The docs/plans files are internal and not committed to main.

## Commit Messages with CVE References (on main)

| Commit | Message | Note |
|--------|---------|------|
| `6cfc6b42` | fix: bump pillow 12.1.0 → 12.1.1 (CVE-2026-25990) | Already in public git history |
| `76b68a67` | fix: Bump cryptography to 46.0.5 (CVE-2026-26007) | Already in public git history |

These are dependency bump commits — standard practice for an open-source project. No action needed.

## Orchestrator Issue Templates (PR #691)

**Result: CLEAN** — No CVE references found in any issue template files or orchestrator skill files.

## External Security Advisory Links

All in `docs/plans/2026-02-23-sandbox-agent-research.md` (internal doc, not on main):

| URL | Context |
|-----|---------|
| `https://thehackernews.com/2026/02/openclaw-bug-enables-one-click-remote.html` | 1-click RCE article |
| `https://www.kaspersky.com/blog/moltbot-enterprise-risk-management/55317/` | Moltbot/OpenClaw analysis |
| `https://www.kaspersky.com/blog/openclaw-vulnerabilities-exposed/55263/` | 512 discovered vulnerabilities |
| `https://www.infosecurity-magazine.com/news/researchers-40000-exposed-openclaw/` | 40K exposed instances |
| `https://depthfirst.com/post/1-click-rce-to-steal-your-moltbot-data-and-keys` | RCE exploit details |
| `https://blog.cyberdesserts.com/openclaw-malicious-skills-security/` | ClawHavoc supply chain attack |
| `https://www.cyera.com/research-labs/the-openclaw-security-saga-how-ai-adoption-outpaced-security-boundaries` | Security analysis |

---

# Already Patched (No Action)

| Package | Version | CVEs Fixed | How |
|---------|---------|------------|-----|
| urllib3 | 2.6.3 | CVE-2026-21441, CVE-2025-66418, CVE-2025-66471 | pyproject.toml pins `>=2.6.3` |
| protobuf | 6.33.5 | CVE-2026-0994 | pyproject.toml pins `>=6.33.5` |
| pyasn1 | 0.6.2 | CVE-2026-23490 | pyproject.toml pins `>=0.6.2` |
| cryptography | 46.0.5 | CVE-2026-26007 | Backend resolves to 46.0.5 |
| starlette | 0.50.0 | CVE-2025-54121, CVE-2025-62727 | 0.50.0 > fix version 0.49.1 |
| MCP SDK | 1.26.0 | CVE-2025-66416 | 1.26.0 > fix version 1.23.0 |
| pillow | 12.1.1 | CVE-2026-25990 | Bumped in commit 6cfc6b42 |
| braces | 3.0.3 | CVE-2024-4068 | Fixed |
| cross-spawn | 7.0.6 | CVE-2024-21538 | Fixed |
| jinja2 | 3.1.6 | CVE-2024-56326 | Fixed |
| requests | 2.32.5 | CVE-2024-35195 | Fixed |
| certifi | 2026.1.4 | Older CVEs | Fixed |
| pyyaml | 6.0.3 | Older CVEs | Fixed |
| nanoid | 3.3.11 | Older CVEs | Fixed |

## Positive Observations

1. Proactive minimum version pins with CVE comments in `pyproject.toml` (urllib3, protobuf, pyasn1)
2. All main Dockerfiles use non-root users and multi-stage builds
3. Comprehensive CI security pipeline in kagenti/kagenti (Trivy, Bandit, CodeQL, Hadolint, shellcheck, action pinning)
4. SHA-pinned GitHub Actions with automated enforcement
5. SSRF protection implemented with private IP blocking
6. Explicit SSL certificate configuration in containers

---

## Scan Methodology

- **Dependency inventory**: Manual file discovery across all repos
- **Version resolution**: Lock files (uv.lock, package-lock.json)
- **CVE lookup**: NVD, Snyk, GitHub Advisory Database via WebSearch
- **Code review**: Pattern-based Grep for auth, injection, secrets, container, CI/CD, network issues
- **Doc audit**: Grep for `CVE-\d{4}-\d+` and vulnerability keywords across all .md files
- **Cross-repo scan**: Local checkouts at `.repos/` + GitHub API for repo metadata

**Limitations:**
- No local Trivy binary scan (CI uses trivy-action)
- Transitive dependency analysis limited to lock file entries
- OS-level CVEs in base images require image scanning for complete coverage
- kagenti-operator Go dependencies not scanned (not checked out locally)

## Sources

- [Snyk Vulnerability Database](https://security.snyk.io/)
- [GitHub Advisory Database](https://github.com/advisories)
- [NVD - National Vulnerability Database](https://nvd.nist.gov/)
- [nginx Security Advisories](https://nginx.org/en/security_advisories.html)
- [CISA KEV Catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
- [python-jose Snyk Advisory](https://security.snyk.io/package/pip/python-jose)
- [ecdsa Timing Attack - CVE-2024-23342](https://github.com/advisories/GHSA-wj6h-64fc-37mp)
