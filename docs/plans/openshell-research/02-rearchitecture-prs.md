# OpenShell Rearchitecture: PR Status

> **Source:** [NVIDIA/OpenShell PRs](https://github.com/NVIDIA/OpenShell/pulls) (public)
> **Last scanned:** 2026-04-16

## Compute Driver Extraction (RFC 0001)

| PR | Title | Status |
|----|-------|--------|
| [#836](https://github.com/NVIDIA/OpenShell/pull/836) | RFC 0001: Core Architecture | Open |
| [#817](https://github.com/NVIDIA/OpenShell/pull/817) | Extract K8s compute driver | **Merged Apr 14** |
| [#839](https://github.com/NVIDIA/OpenShell/pull/839) | ComputeDriver RPC in-process | **Merged Apr 15** |
| [#858](https://github.com/NVIDIA/OpenShell/pull/858) | Standalone VM compute driver | Open |
| [#861](https://github.com/NVIDIA/OpenShell/pull/861) | Supervisor session relay | Open |

RFC 0001 defines four driver subsystems: compute, credentials,
control-plane identity, sandbox identity. Each is a separate process
communicating via gRPC over Unix domain sockets.

## Security PRs

| PR | Title | Status |
|----|-------|--------|
| [#822](https://github.com/NVIDIA/OpenShell/pull/822) | L7 deny rules | **Merged Apr 15** |
| [#826](https://github.com/NVIDIA/OpenShell/pull/826) | Header allowlist | **Merged Apr 15** |
| [#821](https://github.com/NVIDIA/OpenShell/pull/821) | Core dump prevention | **Merged Apr 15** |
| [#819](https://github.com/NVIDIA/OpenShell/pull/819) | Seccomp + SSRF hardening | **Merged Apr 13** |
| [#810](https://github.com/NVIDIA/OpenShell/pull/810) | Two-phase Landlock | **Merged Apr 13** |
| [#860](https://github.com/NVIDIA/OpenShell/pull/860) | Incremental policy updates | Open |
| [#862](https://github.com/NVIDIA/OpenShell/pull/862) | System CA certificates | Open |

## Other

| PR | Title | Status |
|----|-------|--------|
| [#775](https://github.com/NVIDIA/OpenShell/pull/775) | Boot hooks | Open (draft) |
| [#502](https://github.com/NVIDIA/OpenShell/pull/502) | Podman support | Open |
