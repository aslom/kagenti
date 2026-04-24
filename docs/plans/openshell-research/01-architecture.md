# OpenShell Architecture Overview

> **Source:** [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell) (Apache 2.0)

## Components

| Crate | Purpose |
|-------|---------|
| openshell-sandbox | Supervisor — Landlock, seccomp, netns, OPA proxy, SSH |
| openshell-server | Gateway — sandbox lifecycle, gRPC API, persistence |
| openshell-driver-kubernetes | K8s compute driver (extracted in PR #817) |
| openshell-cli | CLI tool |
| openshell-tui | Ratatui interactive TUI |
| openshell-router | Inference routing (provider selection, auth rewrite) |
| openshell-providers | Credential discovery (Anthropic, OpenAI, GitHub, etc.) |
| openshell-ocsf | OCSF v1.7.0 security event logging |
| openshell-prover | Formal policy verification (Z3 SMT solver) |
| openshell-vm | libkrun microVM runtime |
| openshell-policy | YAML policy parsing |
| openshell-core | Shared protos, config, errors |

## Security Layers

1. **Landlock** — kernel filesystem isolation per sandbox
2. **seccomp** — custom BPF syscall filtering (21+ blocked syscalls)
3. **Network namespace** — veth pair forcing all traffic through proxy
4. **OPA/Rego** — per-binary egress policy evaluation
5. **TLS MITM** — credential placeholder resolution at HTTP layer
6. **Binary identity** — SHA256 TOFU for process identification

## Credential Isolation

Agent env vars contain placeholder tokens (`openshell:resolve:env:*`).
Supervisor proxy resolves to real secrets at HTTP layer. Inference router
strips agent auth headers entirely. Agent never sees real credentials.
