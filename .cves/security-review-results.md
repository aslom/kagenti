# Security Review Results - Kagenti

Date: 2026-03-03
Reviewer: Automated source code security review (Claude Code)
Scope: Source code patterns in kagenti/backend/, kagenti/auth/, charts/, .github/workflows/, Dockerfiles

## Summary

| Severity | Count |
|----------|-------|
| HIGH     | 5     |
| MEDIUM   | 9     |
| LOW      | 5     |
| INFO     | 4     |

---

## Findings

### [HIGH] H1: Sandbox Trigger Endpoint Has No Authentication

- **File**: `kagenti/backend/app/routers/sandbox_trigger.py` (entire router)
- **Category**: Auth
- **Description**: The `/api/v1/sandbox/trigger` endpoint has no authentication or authorization. Unlike all other routers (agents, tools, namespaces, config, chat, sandbox_files) which use `require_roles()` or `get_current_user()` dependencies, the sandbox_trigger router has no auth checks at all. This allows any unauthenticated user to create SandboxClaim Kubernetes resources, which could lead to resource exhaustion or unauthorized workload creation.
- **Recommendation**: Add `dependencies=[Depends(require_roles(ROLE_OPERATOR))]` to the `@router.post("/trigger")` decorator or to the router-level `dependencies` parameter, consistent with other write endpoints in the codebase.

---

### [HIGH] H2: Hardcoded Keycloak Admin Credentials in Helm Template

- **File**: `charts/kagenti-deps/templates/keycloak-k8s.yaml:62-64`
- **Category**: Secrets
- **Description**: The Keycloak deployment template contains hardcoded admin credentials (`KC_BOOTSTRAP_ADMIN_USERNAME=admin`, `KC_BOOTSTRAP_ADMIN_PASSWORD=admin`). These values are not parameterized via Helm values and will be deployed with default credentials regardless of configuration. While the `values.yaml` has `adminPassword: your-secret-password`, the actual Keycloak StatefulSet template does not reference this value.
- **Recommendation**: Replace the hardcoded values with Helm template references: `{{ .Values.keycloak.auth.adminUser }}` and `{{ .Values.keycloak.auth.adminPassword }}`. Alternatively, source credentials from a Kubernetes Secret rather than environment variables.

---

### [HIGH] H3: OpenAI API Key Committed in Secret Values File

- **File**: `deployments/envs/.secret_values.yaml:10`
- **Category**: Secrets
- **Description**: A real OpenAI API key (`sk-proj--IUF6i...`) is present in `.secret_values.yaml`. While this file is in `.gitignore`, it exists on disk and could be accidentally committed. A backup file `.secret_values.yaml.bak-openai` also contains the same key and is NOT excluded by `.gitignore` (the ignore rule `.secret_values.yaml` does not match `.secret_values.yaml.bak-openai`).
- **Recommendation**: (1) Rotate the exposed OpenAI API key immediately. (2) Add `*.bak*` or `.secret_values.yaml.*` patterns to `.gitignore`. (3) Replace the key in `.secret_values.yaml` with a placeholder like `sk-REPLACE_WITH_YOUR_KEY`. (4) Consider using a secrets manager (Vault, AWS Secrets Manager) rather than local files.

---

### [HIGH] H4: JWT Audience Validation Disabled

- **File**: `kagenti/backend/app/core/auth.py:200`
- **Category**: Auth
- **Description**: JWT token validation disables audience verification (`"verify_aud": False`). The comment says "Keycloak doesn't always set audience," but this means any valid JWT from the same Keycloak realm can be used to authenticate to the backend API, even if the token was issued for a completely different client/application. This violates the principle of intended audience and could allow token misuse across services in the same realm.
- **Recommendation**: Configure the Keycloak client to include the `aud` claim (via audience mapper) and enable audience verification. If backward compatibility is needed, add an explicit check that the token's `azp` (authorized party) claim matches the expected client ID.

---

### [HIGH] H5: SSRF Protection Bypass via DNS Rebinding and Redirect

- **File**: `kagenti/backend/app/routers/agents.py:2953-2989`
- **Category**: Network
- **Description**: The `fetch_env_from_url` endpoint has SSRF protection that resolves the hostname and checks against blocked IP ranges. However, this is vulnerable to DNS rebinding attacks: an attacker controls a DNS record that initially resolves to a public IP (passing the check), then changes to a private IP before the HTTP client makes the actual request. Additionally, `follow_redirects=True` is enabled, which means a public URL could redirect to an internal service URL, bypassing the IP check entirely since only the initial hostname is validated.
- **Recommendation**: (1) Disable `follow_redirects` or validate each redirect destination's IP. (2) Use the resolved IP address directly for the HTTP request (connect to the IP, set Host header). (3) Consider using a dedicated library for SSRF protection that handles DNS rebinding.

---

### [MEDIUM] M1: Auth Disabled by Default - Mock Admin User Returned

- **File**: `kagenti/backend/app/core/auth.py:267-275` and lines 291-299
- **Category**: Auth
- **Description**: When `enable_auth` is `False` (the default per `config.py:92`), both `get_current_user()` and `get_required_user()` return a mock user with `ROLE_ADMIN` privileges. This means all endpoints are fully accessible without any authentication by default. If a production deployment forgets to set `ENABLE_AUTH=true`, all API endpoints are unprotected.
- **Recommendation**: (1) Consider making auth enabled by default (opt-out rather than opt-in). (2) At minimum, add a startup warning log when auth is disabled. (3) Add a check that prevents `enable_auth=False` when `is_running_in_cluster` returns True.

---

### [MEDIUM] M2: Keycloak JWKS Fetched Over Plain HTTP Without TLS Verification Control

- **File**: `kagenti/backend/app/core/auth.py:83-84`
- **Category**: Network
- **Description**: The JWKS endpoint is fetched using `httpx.AsyncClient()` with no explicit `verify` parameter. The JWKS URL is constructed from `keycloak_internal_url`, which can be an HTTP URL (e.g., `http://keycloak-service.keycloak:8080`). While in-cluster HTTP may be acceptable with Istio mTLS, the default httpx client uses whatever SSL configuration the environment provides, including potentially the Kubernetes service account CA that does not validate public CAs. There is no option to configure TLS verification for this critical security endpoint.
- **Recommendation**: (1) Make JWKS endpoint TLS verification configurable. (2) When running in-cluster with Istio mTLS, document that HTTP is acceptable. (3) For non-Istio deployments, enforce HTTPS for JWKS retrieval.

---

### [MEDIUM] M3: Hardcoded Default Credentials in AuthBridge Demo Deployments

- **Files**:
  - `.repos/kagenti-extensions/AuthBridge/demos/single-target/k8s/authbridge-deployment.yaml:83`
  - `.repos/kagenti-extensions/AuthBridge/demos/multi-target/k8s/authbridge-deployment.yaml:76`
  - `.repos/kagenti-extensions/AuthBridge/demos/webhook/k8s/configmaps-webhook.yaml:24`
  - `.repos/kagenti-extensions/AuthBridge/demos/github-issue/k8s/configmaps.yaml:31`
  - `.repos/kagenti-extensions/AuthBridge/client-registration/example_deployment.yaml:17`
  - `kagenti/examples/identity/keycloak_token_exchange/demo_keycloak_config.py:10`
- **Category**: Secrets
- **Description**: Multiple demo and example deployment manifests contain `KEYCLOAK_ADMIN_PASSWORD: "admin"` as plain text in ConfigMaps and environment variables. While these are demo/example files, they establish a pattern that users may copy into production configurations.
- **Recommendation**: (1) Replace hardcoded passwords with references to Kubernetes Secrets. (2) Add prominent warnings in demo files that credentials must be changed for production. (3) Use `secretKeyRef` instead of plain text values for all credential references.

---

### [MEDIUM] M4: Verbose Error Details Leaked to API Clients

- **Files**:
  - `kagenti/backend/app/core/auth.py:242` - `detail=f"Invalid token: {str(e)}"`
  - `kagenti/backend/app/routers/chat.py:142` - `detail=f"Error fetching agent card: {str(e)}"`
  - `kagenti/backend/app/routers/chat.py:244` - `detail=f"Error sending message: {str(e)}"`
  - `kagenti/backend/app/routers/agents.py:3017` - `detail=f"Unexpected error: {str(e)}"`
  - `kagenti/backend/app/routers/tools.py:2084` - `detail=f"Error connecting to MCP server: {str(e)}"`
- **Category**: Auth / Info Disclosure
- **Description**: Multiple endpoints include raw exception messages in HTTP error responses. This can leak internal details such as internal hostnames, file paths, stack traces, or database error messages to API clients.
- **Recommendation**: Return generic error messages to clients and log detailed errors server-side only. For example: `detail="Authentication failed"` instead of `detail=f"Invalid token: {str(e)}"`.

---

### [MEDIUM] M5: CORS Allows All Methods and All Headers

- **File**: `kagenti/backend/app/main.py:102-103`
- **Category**: Network
- **Description**: CORS middleware is configured with `allow_methods=["*"]` and `allow_headers=["*"]`. While the `allow_origins` is properly restricted to specific domains, the wildcard methods and headers are overly permissive and may allow unexpected HTTP methods (like DELETE, PATCH) or headers to be used in cross-origin requests.
- **Recommendation**: Restrict `allow_methods` to only the methods actually used by the API (e.g., `["GET", "POST", "PUT", "DELETE", "OPTIONS"]`). Restrict `allow_headers` to known required headers (e.g., `["Authorization", "Content-Type", "Accept"]`).

---

### [MEDIUM] M6: No Input Validation on Kubernetes Resource Names

- **File**: `kagenti/backend/app/routers/agents.py:178-182` (`CreateAgentRequest`)
- **Category**: Injection
- **Description**: The `CreateAgentRequest.name` and `CreateAgentRequest.namespace` fields have no validation against Kubernetes naming rules. While Kubernetes itself will reject invalid names, unsanitized names are used in label selectors (e.g., `label_selector=f"app={agent_name}"` in `sandbox_files.py:117` and `label_selector=f"kagenti.io/build-name={name}"` in multiple files). A malicious name containing characters like `,` or `=` could manipulate label selector queries.
- **Recommendation**: Add a `@field_validator` for `name` and `namespace` that enforces RFC 1123 DNS label format: `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`. This is already the pattern used for `EnvVar.name` validation.

---

### [MEDIUM] M7: MLFlow OAuth Secret Dockerfile Runs as Root

- **File**: `kagenti/auth/mlflow-oauth-secret/Dockerfile`
- **Category**: Container
- **Description**: The mlflow-oauth-secret Dockerfile does not specify a `USER` directive. Unlike all other Dockerfiles in the kagenti codebase (backend, ui-v2, ui-oauth-secret, agent-oauth-secret, api-oauth-secret), which correctly switch to a non-root user, this container runs as root. While it's a Job that runs briefly, a vulnerability in the Python code or dependencies could be exploited with root privileges.
- **Recommendation**: Add a non-root user creation and `USER` directive, consistent with other auth Dockerfiles in the project (e.g., `USER 1001`).

---

### [MEDIUM] M8: In-Cluster Service Communication Uses Plain HTTP

- **Files**:
  - `kagenti/backend/app/routers/chat.py:71` - `http://{name}.{namespace}.svc.cluster.local:8080`
  - `kagenti/backend/app/routers/tools.py:2015` - `http://{service_name}.{namespace}.svc.cluster.local:{port}`
  - `kagenti/backend/app/core/constants.py:134` - `http://otel-collector...`
  - `kagenti/backend/app/core/constants.py:138` - `http://keycloak...`
- **Category**: Network
- **Description**: All in-cluster service-to-service communication uses plain HTTP URLs. While Istio ambient mode with mTLS is expected to encrypt this traffic at the mesh level, if Istio is not properly configured or a service is not enrolled in the mesh, traffic including sensitive data (auth tokens, API keys, user messages) would be transmitted in plaintext.
- **Recommendation**: (1) Document the Istio mTLS requirement as a hard security dependency. (2) Consider adding a startup check that verifies Istio mesh enrollment. (3) For Keycloak JWKS validation specifically, consider using HTTPS even in-cluster.

---

### [MEDIUM] M9: JWKS Keys Not Refreshed on Schedule

- **File**: `kagenti/backend/app/core/auth.py:73-112`
- **Category**: Auth
- **Description**: The JWKS keys are fetched once at first token validation and only refreshed if a token's `kid` is not found. There is no periodic refresh mechanism. If Keycloak rotates keys while old keys are still valid, the cached keys will remain stale. More importantly, if an attacker obtains a key that has been revoked in Keycloak but is still cached in the backend, they can continue using tokens signed with that key.
- **Recommendation**: Implement a periodic JWKS refresh (e.g., every 5-15 minutes) using a background task, or add a TTL-based cache that forces re-fetching after a configurable interval.

---

### [LOW] L1: Alpine :latest Tags in Extension Dockerfiles

- **Files**:
  - `.repos/kagenti-extensions/AuthBridge/AuthProxy/Dockerfile:18` - `FROM alpine:latest`
  - `.repos/kagenti-extensions/AuthBridge/AuthProxy/go-processor/Dockerfile:12` - `FROM alpine:latest`
  - `.repos/kagenti-extensions/AuthBridge/AuthProxy/quickstart/demo-app/Dockerfile:21` - `FROM alpine:latest`
- **Category**: Container
- **Description**: Several Dockerfiles use `alpine:latest` as the base image. The `latest` tag is mutable, which means builds are not reproducible and could pull different (potentially vulnerable) versions. The main kagenti Dockerfiles correctly use pinned versions (e.g., `python:3.12-slim`, `alpine:3.21`).
- **Recommendation**: Pin Alpine images to a specific version (e.g., `alpine:3.21`), consistent with the pattern used in the main kagenti Dockerfiles.

---

### [LOW] L2: OpenAPI/Swagger Documentation Exposed by Default

- **File**: `kagenti/backend/app/main.py:91-93`
- **Category**: Info Disclosure
- **Description**: The FastAPI application exposes Swagger UI (`/api/docs`), ReDoc (`/api/redoc`), and OpenAPI schema (`/api/openapi.json`) by default with no authentication. These endpoints reveal the complete API surface, request/response schemas, and authentication requirements, which aids reconnaissance.
- **Recommendation**: Disable API documentation in production by setting `docs_url=None`, `redoc_url=None`, and `openapi_url=None` when `debug` is `False`. Or protect these endpoints with authentication.

---

### [LOW] L3: Uvicorn Binds to 0.0.0.0 in Dev Mode

- **File**: `kagenti/backend/app/main.py:138`
- **Category**: Network
- **Description**: The `if __name__ == "__main__"` development server binds to `0.0.0.0`, exposing the application on all network interfaces. In development this could expose the API to the local network.
- **Recommendation**: This is acceptable for container deployments (where the Dockerfile CMD uses the same pattern). For local development, consider binding to `127.0.0.1` by default with an option to override.

---

### [LOW] L4: Keycloak Admin Role Auto-Mapping

- **File**: `kagenti/backend/app/core/auth.py:227-228`
- **Category**: Auth
- **Description**: Any Keycloak user with the generic `admin` realm role is automatically granted `kagenti-admin` privileges via a temporary mapping. The comment indicates this is a TODO, but if left in place, it means any Keycloak admin from any application in the same realm gets full kagenti-admin access.
- **Recommendation**: Complete the epic referenced in the TODO (issue #647) to provision dedicated Keycloak realm roles, and remove the automatic mapping. Until then, document this behavior as a known security limitation.

---

### [LOW] L5: Health Check Endpoint Uses urllib (No Auth)

- **File**: `kagenti/backend/Dockerfile:64`
- **Category**: Container
- **Description**: The Docker HEALTHCHECK uses `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`. The health endpoint is unauthenticated (by design for K8s probes), but the check imports and executes Python, which adds overhead and attack surface to health checks.
- **Recommendation**: Consider using `curl --fail http://localhost:8000/health` or a dedicated lightweight health check binary, though this is a minor concern since the container already runs Python.

---

### [INFO] I1: Good Practice - SSRF Protection Implemented

- **File**: `kagenti/backend/app/routers/agents.py:2815-2831`
- **Category**: Network
- **Description**: The `fetch_env_from_url` endpoint implements SSRF protection by blocking private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, 169.254.0.0/16). While bypasses exist (see H5), the presence of this protection demonstrates security awareness.

---

### [INFO] I2: Good Practice - SHA-Pinned GitHub Actions

- **File**: `.github/workflows/ci.yaml`, `security-scans.yaml`, `e2e-hypershift-pr.yaml`
- **Category**: CI/CD
- **Description**: All GitHub Actions in the main workflows are pinned to SHA commits with version comments (e.g., `actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6`). This prevents supply chain attacks via compromised action repositories. The `action-pinning` job in security-scans.yaml also enforces this.

---

### [INFO] I3: Good Practice - Comprehensive Security Scanning Pipeline

- **File**: `.github/workflows/security-scans.yaml`
- **Category**: CI/CD
- **Description**: The repository has a multi-phase security scanning pipeline including: dependency review (moderate severity threshold), shellcheck, YAML lint, Helm lint, Bandit (Python security), Hadolint (Dockerfile lint), Trivy (filesystem and config scanning), CodeQL (static analysis), and action pinning verification. Permissions follow the principle of least privilege per job.

---

### [INFO] I4: Good Practice - Non-Root Container User and Multi-Stage Builds

- **Files**: `/kagenti/backend/Dockerfile`, `/kagenti/ui-v2/Dockerfile`, `/kagenti/auth/*/Dockerfile`
- **Category**: Container
- **Description**: The main backend and UI Dockerfiles use multi-stage builds to minimize the final image size and attack surface. All main Dockerfiles create and switch to a non-root user. The backend Dockerfile includes a proper HEALTHCHECK. SSL certificate configuration is explicit, preventing the Kubernetes service account CA from being used for external TLS connections.

---

## Remediation Priority

| Priority | Findings | Effort |
|----------|----------|--------|
| Immediate | H1 (sandbox auth), H3 (API key rotation), H4 (JWT audience) | Low |
| Short-term | H2 (Keycloak defaults), H5 (SSRF bypass), M1 (auth default) | Medium |
| Medium-term | M4 (error leak), M6 (name validation), M7 (root container), M9 (JWKS refresh) | Medium |
| Long-term | M2 (JWKS TLS), M3 (demo creds), M5 (CORS), M8 (in-cluster HTTP) | Low-Medium |
| Optional | L1-L5 (low severity items) | Low |
