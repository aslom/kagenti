# KOSH-SPEC: Kagenti OpenShell CLI Specification

## Overview

**Kosh** (Kagenti OpenShell) is a Python CLI that wraps NVIDIA's `openshell` binary and adds Kagenti-specific commands for managing sandboxed AI agent environments. It supports two runtime modes:

1. **Remote sandboxes** via OpenShell gateway (containers managed by the gateway server)
2. **Local sandboxes** via macOS `sandbox-exec` (sandboxed shell processes on the host)

## Architecture

```
                     +-----------------+
                     |    kosh.py      |  Click CLI (Python 3.11+, uv inline deps)
                     +--------+--------+
                              |
              +---------------+---------------+
              |               |               |
     passthrough cmds    teleport cmd    local-sandbox cmds
     (-> openshell)      (-> teleport.sh) (-> sandbox.sh)
              |               |               |
     +--------+----+   +-----+-----+   +-----+------+
     | openshell   |   | openshell |   | sandbox-   |
     | CLI binary  |   | CLI + SSH |   | exec (macOS)|
     +-------------+   +-----------+   +------------+
```

## Components

| File | Purpose |
|------|---------|
| `kosh` | Shell wrapper (`exec uv run kosh.py "$@"`) — allows running as `./kosh` or adding to PATH |
| `kosh.py` | Main CLI entry point (Click + uv inline metadata) |
| `teleport.sh` | Remote sandbox provisioning script |
| `sandbox.sh` | macOS sandbox-exec wrapper for local shells |
| `agent-sandbox.sb` | macOS SBPL sandbox profile |
| `litellm_sandbox_policy.yaml` | OpenShell network/filesystem policy for remote sandboxes |
| `Dockerfile.sandbox` | Custom sandbox image (base + tmux), opt-in |

---

## 1. kosh.py

**Invocation**: `uv run kosh.py <command> [args...]` or `./kosh <command> [args...]`

**Version**: 0.1.0

**Dependencies** (inline `uv` script metadata):
- `openshell` (Python SDK + CLI)
- `click>=8.0`

### Command Architecture

Uses a custom `KoshGroup` that delegates unknown subcommands to the `openshell` binary. Native kosh commands take priority; unrecognized names are checked against a passthrough allowlist.

### Passthrough Commands

These are forwarded directly to `openshell <cmd> [args...]`:

```
sandbox, gateway, status, forward, logs, policy, settings,
provider, inference, doctor, term, ssh-proxy
```

### Native Commands

#### `kosh completions`

Generate shell completions for kosh.

| Argument | Values | Default | Description |
|----------|--------|---------|-------------|
| `SHELL` | `bash`, `zsh`, `fish` | `zsh` | Target shell |

**Usage**: Add to shell profile:
```bash
eval "$(kosh completions zsh)"    # zsh
eval "$(kosh completions bash)"   # bash
kosh completions fish | source    # fish
```

#### `kosh teleport`

Set up and sync a project into a remote OpenShell sandbox.

| Option | Default | Description |
|--------|---------|-------------|
| `--directory, -d` | last local sandbox | Project directory to teleport |
| `--openshell-bin` | auto-detect | Path to openshell binary |
| `--xdg-config-home` | auto | Override XDG_CONFIG_HOME |
| `--connect / --no-connect` | `--no-connect` | SSH into sandbox after setup |
| `--custom-image` | off | Build from Dockerfile.sandbox |
| `--model` | `aws/claude-opus-4-6` | Claude model for ANTHROPIC_MODEL |

**Behavior**:
1. Resolves project directory (explicit or last local sandbox from config)
2. Sets environment variables (`OPENSHELL_BIN`, `XDG_CONFIG_HOME`, `KOSH_CUSTOM_IMAGE`, `KOSH_MODEL`)
3. Delegates to `teleport.sh` with `cwd` set to project directory
4. Optionally connects via `openshell sandbox connect <name>`

#### `kosh local-sandbox create`

Create a local macOS sandboxed environment.

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | (required) | Sandbox name (used as directory name) |
| `--model` | `aws/claude-opus-4-6` | Claude model for ANTHROPIC_MODEL |

**Requires**: `ANTHROPIC_AUTH_TOKEN` environment variable.

**Behavior**:
1. Creates directory `<cwd>/<name>` if absent
2. Writes `.bashrc` and `.zshrc` with:
   - `ANTHROPIC_BASE_URL` (IBM LiteLLM endpoint)
   - `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`
   - `ANTHROPIC_MODEL=<model>`
3. Registers sandbox in `metadata.json`
4. Saves as last sandbox in `last_local_sandbox`
5. Launches `sandbox.sh zsh` in the sandbox directory

#### `kosh local-sandbox connect`

Reconnect to an existing local sandbox.

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | last used | Sandbox name to connect to |

**Behavior**: Resolves directory from metadata or last sandbox, updates last sandbox pointer, launches `sandbox.sh zsh`.

#### `kosh local-sandbox list`

List all registered local sandboxes with name, status (exists/missing), directory, and last-used marker (`*`).

#### `kosh local-sandbox delete`

Delete a sandbox directory and metadata entry.

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | (required) | Sandbox to delete |

**Behavior**: Prompts for confirmation, removes directory (`shutil.rmtree`), removes from `metadata.json`, clears `last_local_sandbox` if it pointed to the deleted sandbox.

### Configuration

Stored at `$XDG_CONFIG_HOME/kosh/` (defaults to `~/.config/kosh/`):

| File | Format | Purpose |
|------|--------|---------|
| `metadata.json` | `{"sandboxes": {"name": {"directory": "/abs/path"}}}` | Registry of all local sandboxes |
| `last_local_sandbox` | Plain text (one absolute path) | Most recently used sandbox |

---

## 2. teleport.sh

**Invocation**: `bash teleport.sh` (from project directory with `.claude/` folder)

Provisions a remote OpenShell sandbox with project files and environment configuration.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENSHELL_BIN` | `openshell` or `~/.local/bin/openshell` | Path to openshell binary |
| `XDG_CONFIG_HOME` | `<workspace_root>/.config` | Gateway config location |
| `KOSH_MODEL` | `aws/claude-opus-4-6` | Claude model for sandbox .bashrc |
| `KOSH_CUSTOM_IMAGE` | unset | Set to `1` to use Dockerfile.sandbox |
| `ANTHROPIC_AUTH_TOKEN` | (required if no litellm provider) | Token for provider creation |

### Steps

1. **Ensure litellm provider**: Checks `openshell provider list` for `litellm`; creates with `--type generic` and `ANTHROPIC_AUTH_TOKEN` credential if missing
2. **Determine sandbox name**: Uses `basename` of current directory; requires `.claude/` directory to exist
3. **Create sandbox**: If not already existing, creates with `litellm_sandbox_policy.yaml` policy and `litellm` provider; optionally uses `--from Dockerfile.sandbox`
4. **Upload files**: Creates remote directory, uploads project files via `openshell sandbox upload`
5. **Configure .bashrc**: Appends (idempotently) to `/sandbox/.bashrc`:
   - `ANTHROPIC_BASE_URL` (IBM LiteLLM endpoint)
   - `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`
   - `ANTHROPIC_MODEL=<model>`
   - `HOME=<uploaded_dir>`
   - `cd $HOME`

---

## 3. sandbox.sh

**Invocation**: `bash sandbox.sh <shell>` (e.g., `bash sandbox.sh zsh`)

macOS `sandbox-exec` wrapper that creates a hardened, isolated shell environment.

### Security Model

- **Deny-all default**: Everything blocked unless explicitly allowed
- **HOME redirection**: `HOME=<project_dir>` (not real home)
- **PATH filtering**: Only allows `/usr`, `/bin`, `/sbin`, `/opt/homebrew`, and project dir entries
- **Private temp**: Creates `.tmp/` in project dir; denies `/tmp`, `/private/tmp`, `/var/tmp`
- **Environment allowlist**: `env -i` with explicit variable passthrough
- **sandbox-exec shim**: Places a no-op `sandbox-exec` in PATH to prevent nested sandboxing

### Opt-in Features

| Feature | Env Var | Default |
|---------|---------|---------|
| SSH agent | `ENABLE_SSH_AGENT=1` | Blocked |
| Docker | `ENABLE_DOCKER=1` | Blocked |

### Resource Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| File size (`ulimit -f`) | 512 MB | Prevent runaway writes |
| Open FDs (`ulimit -n`) | 4096 | Node.js/libuv watchers |
| Max procs (`ulimit -u`) | 2048 (or `SANDBOX_MAX_PROCS`) | Fork bomb protection |

### Sandbox Parameters (passed to agent-sandbox.sb)

| Parameter | Source |
|-----------|--------|
| `PROJECT_DIR` | Current directory (resolved) |
| `HOST_HOME` | Real `$HOME` |
| `SANDBOX_DIR` | Same as project dir |
| `SSH_AGENT_DIR` | Socket dir or sentinel path |
| `CLAUDE_BIN` | Claude binary directory |
| `DOCKER_SOCK_DIR` | Socket dir or sentinel path |

### Environment Variables Passed Through

**Always set**: `HOME`, `USER`, `LOGNAME`, `SHELL`, `PATH`, `TMPDIR`, `TMPPREFIX`, `CLAUDE_CODE_TMPDIR`, `CLAUDE_CONFIG_DIR`, `LANG`, `TERM`, `__CF_USER_TEXT_ENCODING`, `COMMAND_MODE`, `XPC_FLAGS`, `XPC_SERVICE_NAME`

**Optional (when set in outer env)**: `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`, `CLAUDE_CODE_DEFAULT_MODEL`, `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`, `ENABLE_LSP_TOOL`, `ENABLE_PROMPT_CACHING_1H`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, terminal/pager variables (`COLORTERM`, `TERM_PROGRAM`, `LESS`, `PAGER`, etc.)

---

## 4. agent-sandbox.sb

macOS SBPL (Sandbox Profile Language) profile for `sandbox-exec`. Defines the security boundary for local sandboxes.

### Rule Summary (SBPL last-match-wins ordering)

| # | Category | Policy |
|---|----------|--------|
| 0 | Default | **Deny all** |
| 1 | JIT | Allow dynamic code generation (V8) |
| 2-3 | Process | Allow fork, exec, PID info, signals (same-sandbox only) |
| 4 | PTY | Allow pseudo-terminal |
| 5 | Network | Allow outbound; bind/accept on localhost only |
| 6 | Sysctl | Allow read (hw.ncpu, kern.osversion, etc.) |
| 7 | Mach IPC | Enumerated allowlist: logging, security, DNS, user identity, network config |
| 8 | SHM | Allow apple.shm.notification_center |
| 9 | Prefs | NSGlobalDomain, CFNetwork, SystemConfiguration only |
| 10 | Privileges | Deny PRIV_GLOBAL_PROC_INFO (suppressed) |
| 11 | IOCTL | Allow on `/dev` |
| 12 | SSH | **Deny** SSH agent socket directory |
| 13 | Docker | Allow Docker socket (when enabled) |
| 14 | System libs | Read-only: `/System/Library`, scoped `/Library`, `/usr`, `/bin`, `/sbin`, scoped `/private/etc`, `/opt/homebrew` |
| 15 | Memory map | Executable mapping for system libs, homebrew, project dir |
| 16 | lsof | **Deny** exec and read of `/usr/sbin/lsof` |
| 17 | Broad deny | Deny all file ops on `SANDBOX_DIR` and `HOST_HOME` |
| 18 | Claude binary | Read-only access (no write, prevents self-modification) |
| 19 | Project dir | **Full read/write** access (overrides rule 17) |
| 20 | Temp dirs | `/var/folders` allowed; `/tmp`, `/private/tmp` denied |
| 21 | Devices | Write access to `/dev` (pty/pipes) |

### Key Security Properties

- Agent cannot read files outside project directory (HOME is denied, then project subpath re-allowed)
- Agent cannot modify its own Claude binary
- Agent cannot use `lsof` to inspect other processes
- Agent cannot access SSH keys or Docker unless explicitly opted in
- FSEvents disabled by default (prevents filesystem metadata leaks)
- Network restricted to outbound + localhost binding

---

## 5. litellm_sandbox_policy.yaml

OpenShell sandbox policy for remote sandboxes. Defines filesystem and network access.

```yaml
version: 1
filesystem_policy:
  read_write:
    - /sandbox
    - /tmp
    - /Users
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
```

Only allows network egress to the IBM LiteLLM proxy on port 443, restricted to specific binaries.

---

## 6. Dockerfile.sandbox

Optional custom sandbox image. Extends the OpenShell base image with `tmux`:

```dockerfile
FROM ghcr.io/nvidia/openshell-community/sandboxes/base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tmux && rm -rf /var/lib/apt/lists/*
USER sandbox
```

Opt-in via `kosh teleport --custom-image` or `KOSH_CUSTOM_IMAGE=1`.

---

## Model Configuration

Default model: `aws/claude-opus-4-6`

| Context | Mechanism | Where it lands |
|---------|-----------|----------------|
| Local sandbox | `kosh local-sandbox create --model <m>` | `.bashrc`/`.zshrc` in sandbox dir |
| Remote (kosh) | `kosh teleport --model <m>` | `KOSH_MODEL` env -> teleport.sh -> `/sandbox/.bashrc` |
| Remote (direct) | `KOSH_MODEL=<m> bash teleport.sh` | `/sandbox/.bashrc` |

---

## Dependencies

- **Python 3.11+** with `uv` (for inline script dependencies)
- **openshell CLI** (`uv tool install -U openshell` or pre-built binary)
- **macOS** (for local sandbox mode via `sandbox-exec`)
- **Docker** (optional, for custom sandbox images)
- **ANTHROPIC_AUTH_TOKEN** (required for provider creation and local sandbox)
