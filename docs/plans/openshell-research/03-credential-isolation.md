# OpenShell Credential Isolation

> **Source:** [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell) `crates/openshell-sandbox/src/secrets.rs` (Apache 2.0)

## Placeholder Token Model

OpenShell implements zero-secret credential isolation. The agent process
never has access to real API keys or tokens.

### How It Works

1. Gateway fetches real credentials from provider config
2. Supervisor's `SecretResolver::from_provider_env()` splits into:
   - **Placeholders** (agent env): `ANTHROPIC_API_KEY=openshell:resolve:env:ANTHROPIC_API_KEY`
   - **Real secrets** (resolver memory): placeholder → real value mapping
3. Agent process gets only placeholder strings
4. HTTP CONNECT proxy terminates TLS (MITM with ephemeral CA)
5. Proxy resolves placeholders in HTTP headers to real secrets
6. **Fail-closed**: if any placeholder is unresolved, request is rejected

### What's Filtered from Agent

- `*_ACCESS_TOKEN` env vars (OAuth tokens)
- `VERTEX_ADC` env var (Google ADC credentials)
- Real secrets never appear in `/proc/<pid>/environ`

### Inference Traffic

For LLM calls to `inference.local`:
- Proxy strips `authorization`, `x-api-key`, `host` headers entirely
- Inference router injects backend API key from route config
- Agent-supplied credentials are never forwarded

### Security Properties

| Scenario | Result |
|----------|--------|
| Agent reads env vars | Gets placeholder strings (useless) |
| Agent intercepts HTTP | Sees placeholders, not real tokens |
| Proxy can't resolve placeholder | Request rejected (HTTP 500) |
| Secret contains CR/LF/null | Rejected (CWE-113 prevention) |
