#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "jinja2>=3.1"]
# ///
"""rossoctlx — CLI to manage a running rossocortex proxy.

Usage:
    ./rossoctlx.py version
    ./rossoctlx.py version --control-url http://localhost:8181
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

DEFAULT_CONTROL_URL = "http://localhost:8181"
DEFAULT_PROXY_PORT = 8185
_xdg_config = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
CONFIG_DIR = Path(os.environ.get("ROSSOCORTEX_CONFIG_DIR", str(Path(_xdg_config) / "rossocortex")))
AGENTS_FILE = CONFIG_DIR / "agents.json"
AGENTS_DIR = CONFIG_DIR / "agents"
_local_dir_env = os.environ.get("ROSSOCORTEX_CONTAINER_LOCAL_DIR")
ROSSOCORTEX_CONTAINER_DIR = Path(_local_dir_env) if _local_dir_env else Path(__file__).resolve().parent / "rossocortex-container"
TEMPLATES_DIR = ROSSOCORTEX_CONTAINER_DIR / "templates"
AGENT_PORT_BASE = 13000
PORTS_PER_AGENT = 5
ROSSOCORTEX_SCRIPT = ROSSOCORTEX_CONTAINER_DIR / "rossocortex.py"
PID_FILE = CONFIG_DIR / "rossocortex.pid"
STATE_FILE = CONFIG_DIR / "rossocortex-state.json"


def _is_running() -> int | str | None:
    """Return PID (int) or container ID (str) if rossocortex is running, None otherwise."""
    if not PID_FILE.exists():
        return None
    content = PID_FILE.read_text().strip()
    if content.startswith("container:"):
        container_id = content.split(":", 1)[1]
        import subprocess as sp
        runtime = _find_container_runtime()
        if runtime:
            result = sp.run([runtime, "ps", "-q", "-f", f"name={CONTAINER_NAME}"], capture_output=True, text=True)
            if result.stdout.strip():
                return f"container:{container_id}"
        PID_FILE.unlink(missing_ok=True)
        return None
    try:
        pid = int(content)
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return None


def _save_state(port: int, control_port: int, pid: int, upstream: str, mode: str, **extra):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "pid": pid, "port": port, "control_port": control_port,
        "upstream": upstream, "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    state.update({k: v for k, v in extra.items() if v is not None})
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _port_is_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _find_free_port(start: int, count: int = 2) -> list[int]:
    """Find `count` consecutive free ports starting from `start`."""
    ports = []
    candidate = start
    while len(ports) < count:
        if _port_is_free(candidate):
            ports.append(candidate)
        else:
            ports.clear()
        candidate += 1
        if candidate > start + 100:
            break
    return ports


def _diagnose_start_failure(port: int, control_port: int, upstream: str, no_authbridge: bool, output: str, returncode: int):
    """Analyze rossocortex startup failure and print root cause."""
    import os
    print(f"\n--- Diagnosis ---", file=sys.stderr)

    if output.strip():
        print(f"Process output:", file=sys.stderr)
        for line in output.strip().splitlines()[-20:]:
            print(f"  {line}", file=sys.stderr)
        print(file=sys.stderr)

    if "No module named" in output:
        module = output.split("No module named")[-1].strip().strip("'\"")
        print(f"Root cause: Missing Python dependency '{module}'", file=sys.stderr)
        print(f"  Fix: uv run --no-project --script rossocortex.py ...", file=sys.stderr)
        return

    if "Address already in use" in output or "OSError" in output:
        print(f"Root cause: Port conflict", file=sys.stderr)
        for p in (port, control_port):
            if not _port_is_free(p):
                _show_port_owner(p)
        print(f"  Fix: kill the process or use --port / --control-port", file=sys.stderr)
        return

    if not no_authbridge:
        binary = ROSSOCORTEX_SCRIPT.parent / "bin" / "authbridge-proxy"
        config = Path(os.environ.get("ROSSOCORTEX_CONFIG_DIR", Path.home() / ".config" / "rossocortex")) / "config.yaml"

        if "binary not found" in output or not binary.exists():
            print(f"Root cause: AuthBridge binary missing", file=sys.stderr)
            print(f"  Expected: {binary}", file=sys.stderr)
            print(f"  Fix: cd {ROSSOCORTEX_SCRIPT.parent} && uv run python authbridge_wrapper.py build", file=sys.stderr)
            return

        if "config not found" in output or not config.exists():
            print(f"Root cause: AuthBridge config missing", file=sys.stderr)
            print(f"  Expected: {config}", file=sys.stderr)
            print(f"  Fix: cd {ROSSOCORTEX_SCRIPT.parent} && uv run python authbridge_wrapper.py init --budget 5.00", file=sys.stderr)
            return

        if "AuthBridge exited immediately" in output:
            print(f"Root cause: AuthBridge subprocess crashed", file=sys.stderr)
            if "address already in use" in output:
                import re
                bind_match = re.search(r"listen tcp [^:]*:(\d+): bind: address already in use", output)
                if bind_match:
                    blocked_port = int(bind_match.group(1))
                    print(f"  AuthBridge port {blocked_port} is already in use", file=sys.stderr)
                    _show_port_owner(blocked_port)
                else:
                    ab_ports = _get_authbridge_ports(config)
                    for p in ab_ports:
                        if not _port_is_free(p):
                            _show_port_owner(p)
            else:
                ab_ports = _get_authbridge_ports(config)
                for p in ab_ports:
                    if not _port_is_free(p):
                        _show_port_owner(p)
            print(f"  Fix: kill conflicting processes, or use --no-authbridge for direct mode", file=sys.stderr)
            return

    if "No credential found" in output:
        print(f"Root cause: No API key available (direct mode requires a credential)", file=sys.stderr)
        print(f"  Fix: export ANTHROPIC_AUTH_TOKEN=sk-... (or LITELLM_API_KEY)", file=sys.stderr)
        return

    if not upstream:
        print(f"Root cause: No upstream URL configured", file=sys.stderr)
        print(f"  Fix: --upstream <URL> or export ROSSOCORTEX_UPSTREAM=...", file=sys.stderr)
        return

    print(f"Root cause: Unknown (exit code {returncode})", file=sys.stderr)
    print(f"  Try running rossocortex.py directly to see full output:", file=sys.stderr)
    print(f"  {ROSSOCORTEX_SCRIPT} --budget 5.00 --upstream {upstream or '<URL>'} --port {port}", file=sys.stderr)


def _show_port_owner(port: int):
    """Show which process holds a port."""
    import subprocess as sp
    try:
        result = sp.run(
            ["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-nP", "-t"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().splitlines()
        if pids:
            for pid in pids[:3]:
                ps = sp.run(["ps", "-p", pid, "-o", "pid=,command="], capture_output=True, text=True, timeout=5)
                cmd_line = ps.stdout.strip()
                print(f"  Port {port} held by: {cmd_line}", file=sys.stderr)
        else:
            print(f"  Port {port} in use (cannot identify owner)", file=sys.stderr)
    except (FileNotFoundError, PermissionError, OSError, sp.TimeoutExpired):
        print(f"  Port {port} in use (lsof unavailable or permission denied)", file=sys.stderr)


def _get_authbridge_ports(config_path: Path) -> list[int]:
    """Extract port numbers from an authbridge config.yaml."""
    ports = []
    if not config_path.exists():
        return [3130, 18081, 18082, 19095, 19096]
    try:
        for line in config_path.read_text().splitlines():
            stripped = line.strip()
            if "_addr" in stripped and ":" in stripped:
                part = stripped.split(":")[-1].strip().strip('"').strip("'")
                if part.isdigit():
                    ports.append(int(part))
    except OSError:
        pass
    return ports or [3130, 18081, 18082, 19095, 19096]


def _run_authbridge_wrapper(subcmd: list[str]) -> int:
    """Run authbridge_wrapper.py with its dependencies. Returns exit code."""
    import subprocess as sp
    wrapper = ROSSOCORTEX_SCRIPT.parent / "authbridge_wrapper.py"
    if not wrapper.exists():
        print(f"  ERROR: authbridge_wrapper.py not found at {wrapper}", file=sys.stderr)
        return 1
    cmd = ["uv", "run", "--no-project", "--with", "click>=8.0", "--with", "jinja2>=3.1",
           "python", str(wrapper)] + subcmd
    print(f"  $ {' '.join(cmd)}")
    result = sp.run(cmd, cwd=str(ROSSOCORTEX_SCRIPT.parent))
    return result.returncode


def _ensure_authbridge_binary() -> bool:
    """Build authbridge-proxy if missing. Returns True if binary is available."""
    binary = ROSSOCORTEX_SCRIPT.parent / "bin" / "authbridge-proxy"
    if binary.exists():
        return True

    print("AuthBridge binary not found — building automatically...")
    rc = _run_authbridge_wrapper(["build"])
    if rc != 0:
        print(f"  Build failed (exit code {rc})", file=sys.stderr)
        return False
    return binary.exists()


def _ensure_authbridge_config(budget: float) -> bool:
    """Config is always generated at startup by rossocortex.py — just ensure CA exists."""
    config_dir = Path(os.environ.get("ROSSOCORTEX_CONFIG_DIR", Path.home() / ".config" / "rossocortex"))
    ca_cert = config_dir / "ca" / "tls.crt"
    if ca_cert.exists():
        return True

    print("CA certificate not found — initializing...")
    rc = _run_authbridge_wrapper(["init", "--budget", str(budget)])
    if rc != 0:
        print(f"  Init failed (exit code {rc})", file=sys.stderr)
        return False
    return ca_cert.exists()


# Credential names in priority order (files checked first, then env vars).
# Mirrors rossocortex.py's credential lookup order.
CREDENTIAL_NAMES = ("LITELLM_API_KEY", "ROSSOCORTEX_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")


def _find_credential() -> tuple[str, str] | None:
    """Return (source, name) of the first available LiteLLM credential, or None.

    Checks credential files in CONFIG_DIR/credentials first, then environment
    variables, following the same priority order rossocortex uses at runtime.
    """
    creds_dir = CONFIG_DIR / "credentials"
    for name in CREDENTIAL_NAMES:
        f = creds_dir / name
        try:
            if f.exists() and f.read_text().strip():
                return (f"file {f}", name)
        except OSError:
            pass
    for name in CREDENTIAL_NAMES:
        if os.environ.get(name, "").strip():
            return (f"env ${name}", name)
    return None


def _check_credential_prereq() -> bool:
    """Verify a LiteLLM API key is available. Print an actionable message if not."""
    found = _find_credential()
    if found:
        return True
    creds_dir = CONFIG_DIR / "credentials"
    key_file = creds_dir / "LITELLM_API_KEY"
    print("ERROR: No LiteLLM API key found — rossocortex has no credential to inject.", file=sys.stderr)
    print("  rossocortex proxies to LiteLLM and must hold a valid virtual key.", file=sys.stderr)
    print("  Provide one of the following (checked in this order):", file=sys.stderr)
    print("    1. A credential file (recommended, persists across restarts):", file=sys.stderr)
    print(f"         mkdir -p {creds_dir}", file=sys.stderr)
    print(f"         printf '%s' 'sk-your-litellm-key' > {key_file}", file=sys.stderr)
    print(f"         chmod 600 {key_file}", file=sys.stderr)
    print("    2. An environment variable (auto-saved to the credential file on first start):", file=sys.stderr)
    print("         export ANTHROPIC_AUTH_TOKEN=sk-your-litellm-key", file=sys.stderr)
    print("  Note: use a LiteLLM virtual key, NOT a raw provider key (e.g. sk-ant-...).", file=sys.stderr)
    return False


def cmd_start(port: int, control_port: int, upstream: str, budget: float, no_authbridge: bool):
    """Start rossocortex as a background daemon."""
    pid = _is_running()
    if pid:
        state = _load_state()
        print(f"rossocortex already running (pid={pid}, port={state.get('port', '?')})")
        return

    if not upstream:
        import os
        upstream = os.environ.get("ROSSOCORTEX_UPSTREAM") or os.environ.get("ANTHROPIC_BASE_URL") or ""
    if not upstream:
        print("ERROR: --upstream required (or set ROSSOCORTEX_UPSTREAM / ANTHROPIC_BASE_URL)", file=sys.stderr)
        sys.exit(1)

    if not _check_credential_prereq():
        sys.exit(1)

    if not _port_is_free(port) or not _port_is_free(control_port):
        free = _find_free_port(port, 2)
        if len(free) < 2:
            print(f"ERROR: Cannot find free ports near {port}", file=sys.stderr)
            for p in (port, control_port):
                if not _port_is_free(p):
                    _show_port_owner(p)
            sys.exit(1)
        port, control_port = free[0], free[1]
        print(f"Ports in use, using: proxy={port}, control={control_port}")

    if not no_authbridge:
        if not _ensure_authbridge_binary():
            print(f"  Or start in direct mode: {sys.argv[0]} start --no-authbridge", file=sys.stderr)
            sys.exit(1)
        if not _ensure_authbridge_config(budget):
            print(f"  Or start in direct mode: {sys.argv[0]} start --no-authbridge", file=sys.stderr)
            sys.exit(1)

    cmd = [
        str(ROSSOCORTEX_SCRIPT),
        "--budget", str(budget),
        "--upstream", upstream,
        "--port", str(port),
        "--control-port", str(control_port),
    ]
    if no_authbridge:
        cmd.append("--no-authbridge")

    import subprocess, tempfile
    log_file = tempfile.NamedTemporaryFile(mode="w", prefix="rossocortex-", suffix=".log", delete=False)
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)

    import time
    wait_secs = 5 if not no_authbridge else 2
    time.sleep(wait_secs)
    log_file.flush()
    if proc.poll() is not None:
        log_file.close()
        output = Path(log_file.name).read_text()
        print(f"ERROR: rossocortex exited immediately (code {proc.returncode})", file=sys.stderr)
        _diagnose_start_failure(port, control_port, upstream, no_authbridge, output, proc.returncode)
        Path(log_file.name).unlink(missing_ok=True)
        sys.exit(1)

    log_file.close()
    Path(log_file.name).unlink(missing_ok=True)

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    mode = "direct" if no_authbridge else "authbridge"
    import os
    config_dir = Path(os.environ.get("ROSSOCORTEX_CONFIG_DIR", Path.home() / ".config" / "rossocortex"))
    creds_dir = config_dir / "credentials"
    cred_files = sorted(creds_dir.iterdir()) if creds_dir.exists() else []
    cred_names = ', '.join(f.name for f in cred_files) if cred_files else 'none'
    ca_cert = config_dir / "ca" / "tls.crt"
    local_dir = str(ROSSOCORTEX_CONTAINER_DIR)
    authbridge_info = None
    if not no_authbridge:
        build_info_path = ROSSOCORTEX_CONTAINER_DIR / "BUILD_INFO.json"
        if build_info_path.exists():
            import json as _json
            bi = _json.loads(build_info_path.read_text())
            authbridge_info = f"{bi.get('authbridge_commit','?')} ({bi.get('authbridge_repo','')}) built {bi.get('built_at','?')}"
    _save_state(port, control_port, proc.pid, upstream, mode,
                local_dir=local_dir, budget=budget,
                credentials=cred_names,
                ca_cert=str(ca_cert) if ca_cert.exists() else None,
                config_dir=str(config_dir),
                authbridge=authbridge_info,
                command=' '.join(cmd))
    _print_banner()

    log_file = _log_file()
    if log_file.exists():
        recent = log_file.read_text().splitlines()[-10:]
        if recent:
            for line in recent:
                print(line)


CONTAINER_NAME = "rossocortex"


def _find_container_runtime() -> str | None:
    import shutil
    preferred = os.environ.get("ROSSOCORTEX_RUNTIME", "")
    if preferred and shutil.which(preferred):
        return preferred
    for cmd in ("docker", "podman"):
        if shutil.which(cmd):
            return cmd
    return None


def cmd_start_container(port: int, control_port: int, upstream: str, budget: float, image: str):
    """Start rossocortex as a container."""
    import subprocess as sp

    runtime = _find_container_runtime()
    if not runtime:
        print("ERROR: docker or podman required for --container mode", file=sys.stderr)
        sys.exit(1)

    existing = sp.run([runtime, "ps", "-q", "-f", f"name={CONTAINER_NAME}"],
                      capture_output=True, text=True)
    if existing.stdout.strip():
        print(f"rossocortex container already running")
        state = _load_state()
        print(f"  port={state.get('port', port)}, control={state.get('control_port', control_port)}")
        return

    if not upstream:
        upstream = os.environ.get("ROSSOCORTEX_UPSTREAM") or os.environ.get("ANTHROPIC_BASE_URL") or ""
    if not upstream:
        print("ERROR: --upstream required (or set ROSSOCORTEX_UPSTREAM)", file=sys.stderr)
        sys.exit(1)

    if not _check_credential_prereq():
        sys.exit(1)

    if not _port_is_free(port) or not _port_is_free(control_port):
        free = _find_free_port(port, 2)
        if len(free) < 2:
            print(f"ERROR: Cannot find free ports near {port}", file=sys.stderr)
            sys.exit(1)
        port, control_port = free[0], free[1]
        print(f"Ports in use, using: proxy={port}, control={control_port}")

    config_dir = Path(os.environ.get("ROSSOCORTEX_CONFIG_DIR", Path.home() / ".config" / "rossocortex"))
    creds_dir = config_dir / "credentials"
    ca_dir = config_dir / "ca"

    creds_dir.mkdir(parents=True, exist_ok=True)
    ca_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("LITELLM_API_KEY") or ""
    if api_key:
        cred_file = creds_dir / "ANTHROPIC_AUTH_TOKEN"
        if not cred_file.exists():
            cred_file.write_text(api_key)
            cred_file.chmod(0o600)

    sp.run([runtime, "rm", "-f", CONTAINER_NAME], capture_output=True)

    cmd = [
        runtime, "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{port}:{port}",
        "-p", f"{control_port}:{control_port}",
        "-v", f"{config_dir}:/etc/rossocortex",
        "-e", f"ROSSOCORTEX_UPSTREAM={upstream}",
        "-e", f"ROSSOCORTEX_PORT={port}",
        "-e", f"ROSSOCORTEX_CONTROL_PORT={control_port}",
        "-e", f"ROSSOCORTEX_DAILY_BUDGET={budget}",
    ]

    cmd.append(image)

    print(f"Starting rossocortex container ({runtime})...")
    print(f"  $ {' '.join(cmd)}")
    result = sp.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: container start failed", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    container_id = result.stdout.strip()[:12]

    import time
    time.sleep(3)

    check = sp.run([runtime, "ps", "-q", "-f", f"name={CONTAINER_NAME}"], capture_output=True, text=True)
    if not check.stdout.strip():
        print("ERROR: container exited immediately", file=sys.stderr)
        logs = sp.run([runtime, "logs", CONTAINER_NAME], capture_output=True, text=True)
        print(logs.stdout[-500:] if logs.stdout else "", file=sys.stderr)
        print(logs.stderr[-500:] if logs.stderr else "", file=sys.stderr)
        sys.exit(1)

    docker_cmd = ' '.join(cmd)
    cred_names = ', '.join(f.name for f in sorted(creds_dir.iterdir())) if creds_dir.exists() else 'none'
    ca_cert = ca_dir / "tls.crt"
    _save_state(port, control_port, 0, upstream, "container",
                image=image, runtime=runtime, container_id=container_id,
                docker_cmd=docker_cmd, budget=budget,
                credentials=cred_names,
                ca_cert=str(ca_cert) if ca_cert.exists() else None,
                config_dir=str(config_dir))
    PID_FILE.write_text(f"container:{container_id}")

    _print_banner()

    log_file = _log_file()
    if log_file.exists():
        recent = log_file.read_text().splitlines()[-10:]
        if recent:
            for line in recent:
                print(line)


def cmd_stop():
    """Stop running rossocortex daemon or container."""
    import subprocess as sp
    stopped = False

    runtime = _find_container_runtime()
    if runtime:
        result = sp.run([runtime, "ps", "-q", "-f", f"name={CONTAINER_NAME}"], capture_output=True, text=True)
        if result.stdout.strip():
            sp.run([runtime, "stop", CONTAINER_NAME], capture_output=True)
            sp.run([runtime, "rm", "-f", CONTAINER_NAME], capture_output=True)
            print(f"rossocortex container stopped")
            stopped = True

    pid_content = PID_FILE.read_text().strip() if PID_FILE.exists() else ""
    if pid_content and not pid_content.startswith("container:"):
        try:
            pid = int(pid_content)
            os.kill(pid, 15)
            print(f"rossocortex stopped (pid={pid})")
            stopped = True
        except (ValueError, ProcessLookupError, PermissionError):
            pass

    PID_FILE.unlink(missing_ok=True)
    state = _load_state()
    if state:
        state["pid"] = 0
        state["mode"] = "stopped"
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")

    if not stopped:
        print("rossocortex is not running")


def _ensure_running(control_url: str) -> bool:
    """Check if rossocortex is running, return True if yes."""
    try:
        httpx.get(f"{control_url}/version", timeout=2.0)
        return True
    except httpx.ConnectError:
        return False


def _runtime_label() -> str:
    """Return 'container' or 'local' based on how rossocortex is running."""
    state = _load_state()
    mode = state.get("mode", "")
    if mode == "container":
        return "container"
    return "local"


def cmd_status(control_url: str):
    try:
        resp = httpx.get(f"{control_url}/version", timeout=3.0)
        data = resp.json()
        label = _runtime_label()
        print(f"rossocortex is running ({label}, pid={data['pid']})")
        print(f"  Mode:       {data['mode']}")
        print(f"  Budget:     ${data['budget']['spent_today']:.4f} / ${data['budget']['daily_limit']:.2f} ({data['budget']['calls_today']} calls)")
        print(f"  Upstream:   {data['upstream']}")
        print(f"  Control:    {control_url}")
        print(f"  Config:     {CONFIG_DIR}")

        agents_data = _load_agents()
        agents = agents_data.get("agents", {})
        if agents:
            print(f"  Agents ({len(agents)}):")
            for name, info in agents.items():
                agent_budget = info.get("budget")
                spend_data = _load_agent_spend(name)
                spent = spend_data.get("total_spend", 0.0)
                calls = spend_data.get("total_calls", 0)
                budget_str = f"${agent_budget:.2f}" if agent_budget else "unlimited"
                print(f"    {name}: ${spent:.4f} / {budget_str} ({calls} calls)")
    except httpx.ConnectError:
        state = _load_state()
        if state:
            print(f"rossocortex is NOT running (stale state: port={state.get('port')}, mode={state.get('mode')})")
            print(f"  Config: {CONFIG_DIR}")
            print(f"  Tried:  {control_url}")
        else:
            print(f"rossocortex is NOT running")
            print(f"  Config: {CONFIG_DIR}")
        sys.exit(1)
    except Exception as e:
        print(f"rossocortex status unknown: {e}")
        print(f"  Config: {CONFIG_DIR}")
        sys.exit(2)


def cmd_version(control_url: str):
    try:
        resp = httpx.get(f"{control_url}/version", timeout=5.0)
    except httpx.ConnectError:
        print(f"ERROR: Cannot connect to rossocortex at {control_url}", file=sys.stderr)
        print(f"  Is rossocortex.py running?", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"ERROR: Control API returned {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    label = _runtime_label()

    print(f"rossocortex {data['rossocortex_version']} ({label})")
    print(f"  Runtime:    {label}")
    print(f"  Mode:       {data['mode']}")
    print(f"  Upstream:   {data['upstream']}")
    print(f"  Proxy port: {data['port']}")
    print(f"  Control:    {control_url}")
    print(f"  PID:        {data['pid']}")
    print()

    budget = data.get("budget", {})
    print(f"Budget:")
    print(f"  Daily limit: ${budget.get('daily_limit', 0):.2f}")
    print(f"  Spent today: ${budget.get('spent_today', 0):.4f}")
    print(f"  Calls today: {budget.get('calls_today', 0)}")
    print()

    ab = data.get("authbridge")
    if ab:
        print(f"AuthBridge:")
        print(f"  Binary:   {ab.get('binary', 'unknown')}")
        print(f"  Commit:   {ab.get('commit', 'unknown')} ({ab.get('repo', '')} {ab.get('branch', '')})")
        print(f"  Go:       {ab.get('go_version', 'unknown')}")
        print(f"  Built:    {ab.get('built_at', 'unknown')}")
        print(f"  Platform: {ab.get('platform', 'unknown')}")
        plugins = ab.get("plugins", [])
        print(f"  Plugins ({len(plugins)}):")
        for p in plugins:
            print(f"    - {p}")
    else:
        print(f"AuthBridge: not active (direct mode)")


def _load_agents() -> dict:
    if not AGENTS_FILE.exists():
        return {"agents": {}}
    try:
        return json.loads(AGENTS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"agents": {}}


def _save_agents(data: dict):
    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENTS_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _allocate_ports(agents: dict) -> dict:
    """Allocate next available port block for a new agent, skipping in-use ports."""
    used_bases = set()
    for info in agents.values():
        ports = info.get("ports", {})
        if ports.get("forward"):
            used_bases.add(ports["forward"] - (ports["forward"] - AGENT_PORT_BASE) % PORTS_PER_AGENT)
    base = AGENT_PORT_BASE
    while True:
        if base in used_bases:
            base += PORTS_PER_AGENT
            continue
        if all(_port_is_free(base + i) for i in range(PORTS_PER_AGENT)):
            break
        base += PORTS_PER_AGENT
        if base > AGENT_PORT_BASE + 500:
            raise RuntimeError("Cannot find free port block for agent authbridge")
    return {
        "forward": base,
        "reverse": base + 1,
        "transparent": base + 2,
        "stats": base + 3,
        "session": base + 4,
    }


def _generate_agent_config(agent_name: str, ports: dict, budget: float | None, credentials_path: str | None = None):
    """Render authbridge config.yaml for an agent."""
    agent_dir = AGENTS_DIR / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)

    creds_dir = agent_dir / "credentials"
    if credentials_path:
        creds_dir = Path(credentials_path)
    elif not creds_dir.exists():
        shared_creds = CONFIG_DIR / "credentials"
        if shared_creds.exists():
            creds_dir.symlink_to(shared_creds)
        else:
            creds_dir.mkdir(parents=True, exist_ok=True)

    ca_dir = CONFIG_DIR / "ca"
    spend_file = agent_dir / "spend-authbridge.json"
    config_file = agent_dir / "config.yaml"

    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError:
        print("ERROR: jinja2 required for config generation. Install: pip install jinja2", file=sys.stderr)
        sys.exit(1)

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("config.yaml.j2")
    rendered = template.render(
        port=ports["forward"],
        reverse_proxy_port=ports["reverse"],
        transparent_port=ports["transparent"],
        session_port=ports["session"],
        stats_port=ports["stats"],
        ca_dir=str(ca_dir),
        credentials_dir=str(creds_dir),
        inference_parser=True,
        mcp_parser=True,
        budget_track=budget is not None and budget > 0,
        spend_file=str(spend_file),
        max_budget=budget or 0,
    )
    config_file.write_text(rendered)
    return config_file


def _load_agent_spend(agent_name: str) -> dict:
    path = CONFIG_DIR / f"spend-{agent_name}.json"
    if not path.exists():
        return {"total_spend": 0.0, "total_calls": 0}
    try:
        data = json.loads(path.read_text())
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if data.get("date") != today:
            return {"total_spend": 0.0, "total_calls": 0}
        return data
    except (json.JSONDecodeError, OSError):
        return {"total_spend": 0.0, "total_calls": 0}


def cmd_agent_id(agent_name: str | None, proxy_port: int, list_agents: bool, delete: bool = False, budget: float | None = None, credentials: str | None = None, network_allow: list[str] | None = None, network_deny: list[str] | None = None):
    if delete:
        if not agent_name:
            print("ERROR: agent_name required with --delete", file=sys.stderr)
            sys.exit(1)
        data = _load_agents()
        agents = data.get("agents", {})
        if agent_name not in agents:
            print(f"ERROR: agent '{agent_name}' not found", file=sys.stderr)
            sys.exit(1)
        del agents[agent_name]
        _save_agents(data)
        import shutil
        agent_dir = AGENTS_DIR / agent_name
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
        spend_file = CONFIG_DIR / f"spend-{agent_name}.json"
        spend_file.unlink(missing_ok=True)
        print(f"Deleted agent '{agent_name}'")
        return

    if list_agents:
        data = _load_agents()
        agents = data.get("agents", {})
        if not agents:
            print("No registered agents.")
            return
        print(f"Registered agents ({len(agents)}):")
        for name, info in agents.items():
            agent_budget = info.get("budget")
            spend_data = _load_agent_spend(name)
            spent = spend_data.get("total_spend", 0.0)
            calls = spend_data.get("total_calls", 0)
            budget_str = f"--budget={agent_budget:.2f}" if agent_budget else "--budget=unlimited"
            allow = info.get("network_allow", [])
            deny = info.get("network_deny", [])
            policy_parts = []
            for h in allow:
                policy_parts.append(f"--network-allow='{h}'" if any(c in h for c in '*?[]') else f"--network-allow={h}")
            for h in deny:
                policy_parts.append(f"--network-deny='{h}'" if any(c in h for c in '*?[]') else f"--network-deny={h}")
            policy_str = f"  {' '.join(policy_parts)}" if policy_parts else ""
            print(f"  {name}  {budget_str}{policy_str}  spent=${spent:.4f}  ({calls} calls)")
        return

    if not agent_name:
        print("ERROR: agent_name required (or use --list)", file=sys.stderr)
        sys.exit(1)

    data = _load_agents()
    agents = data.setdefault("agents", {})

    is_new = agent_name not in agents
    if not is_new:
        token = agents[agent_name]["token"]
        changed = False
        if budget is not None:
            if budget == 0:
                agents[agent_name].pop("budget", None)
            else:
                agents[agent_name]["budget"] = budget
            changed = True
        if credentials is not None:
            agents[agent_name]["credentials"] = credentials
            changed = True
        if network_allow is not None:
            agents[agent_name]["network_allow"] = network_allow
            changed = True
        if network_deny is not None:
            agents[agent_name]["network_deny"] = network_deny
            changed = True
        if "ports" not in agents[agent_name]:
            agents[agent_name]["ports"] = _allocate_ports(agents)
            changed = True
        if changed:
            _save_agents(data)
            _generate_agent_config(
                agent_name, agents[agent_name]["ports"],
                agents[agent_name].get("budget"),
                agents[agent_name].get("credentials"),
            )
    else:
        token = secrets.token_hex(16)
        ports = _allocate_ports(agents)
        entry = {
            "token": token,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ports": ports,
        }
        if budget is not None and budget != 0:
            entry["budget"] = budget
        if credentials is not None:
            entry["credentials"] = credentials
        if network_allow is not None:
            entry["network_allow"] = network_allow
        if network_deny is not None:
            entry["network_deny"] = network_deny
        agents[agent_name] = entry
        _save_agents(data)
        _generate_agent_config(agent_name, ports, budget, credentials)

    # Print what changed
    info = agents[agent_name]
    changes = []
    if budget is not None:
        changes.append("budget=unlimited" if budget == 0 else f"budget={budget:.2f}")
    if credentials is not None:
        changes.append(f"credentials={credentials}")
    if network_allow is not None:
        changes.append(f"network-allow={','.join(network_allow)}")
    if network_deny is not None:
        changes.append(f"network-deny={','.join(network_deny)}")
    if is_new:
        print(f"# Created agent '{agent_name}'" + (f" with {', '.join(changes)}" if changes else ""))
    elif changes:
        print(f"# Updated agent '{agent_name}': {', '.join(changes)}")
    else:
        print(f"# Agent '{agent_name}' (no changes)")

    # Print agent config in CLI-flag format (same as 'agents' command)
    agent_budget = info.get("budget")
    spend_data = _load_agent_spend(agent_name)
    spent = spend_data.get("total_spend", 0.0)
    calls = spend_data.get("total_calls", 0)
    budget_flag = f"--budget={agent_budget:.2f}" if agent_budget else "--budget=unlimited"
    allow = info.get("network_allow", [])
    deny = info.get("network_deny", [])
    _q = lambda h: f"'{h}'" if any(c in h for c in '*?[]') else h
    policy_parts = [f"--network-allow={_q(h)}" for h in allow] + [f"--network-deny={_q(h)}" for h in deny]
    policy_str = f"  {' '.join(policy_parts)}" if policy_parts else ""
    print(f"#   {agent_name}  {budget_flag}{policy_str}  spent=${spent:.4f}  ({calls} calls)")

    state = _load_state()
    upstream = state.get("upstream", "")
    actual_port = state.get("port", proxy_port)
    ca_cert = CONFIG_DIR / "ca" / "tls.crt"

    base_url = f"http://{agent_name}:{token}@localhost:{actual_port}"
    plain_url = f"http://localhost:{actual_port}"
    agent_key = f"{agent_name}:{token}"
    print(f"# Run with: eval \"$({sys.argv[0]} agent {agent_name})\"")
    print(f"export OPENAI_API_BASE={base_url}")
    print(f"export OPENAI_API_KEY={agent_key}")
    print(f"export ANTHROPIC_BASE_URL={plain_url}")
    print(f"export ANTHROPIC_AUTH_TOKEN={agent_key}")
    if upstream:
        print(f"export HTTPS_PROXY=http://{agent_name}:{token}@localhost:{actual_port}")
        print(f"export NO_PROXY=localhost,127.0.0.1")
        if ca_cert.exists():
            print(f"export SSL_CERT_FILE={ca_cert}")


def _print_completion_code(shell_name: str, aliases: list[str] | None = None):
    """Output raw completion code suitable for eval."""
    if shell_name == "zsh":
        print('autoload -Uz compinit 2>/dev/null; compinit 2>/dev/null')
        print('_rossoctlx() { local commands="status version start stop log logs agent agents completions"; if (( CURRENT == 2 )); then _describe "command" "(status:Check\\ if\\ running version:Show\\ version start:Start\\ daemon stop:Stop\\ daemon log:Show\\ request\\ log logs:Show\\ request\\ log agent:Manage\\ agents agents:List\\ agents completions:Shell\\ completion\\ setup)"; fi }')
        print('compdef _rossoctlx rossoctlx.py')
        print('compdef _rossoctlx rossoctlx')
        for alias in (aliases or []):
            print(f'compdef _rossoctlx {alias}')
    elif shell_name == "bash":
        print('_rossoctlx() { COMPREPLY=($(compgen -W "status version start stop log logs agent agents completions" -- "${COMP_WORDS[COMP_CWORD]}")); }')
        print('complete -o default -F _rossoctlx rossoctlx.py')
        print('complete -o default -F _rossoctlx rossoctlx')
        for alias in (aliases or []):
            print(f'complete -o default -F _rossoctlx {alias}')
    elif shell_name == "fish":
        for cmd in ("rossoctlx.py", "rossoctlx", *(aliases or [])):
            print(f"complete -c {cmd} -f -n '__fish_use_subcommand' -a 'status version start stop log logs agent agents completions'")


def cmd_completions(eval_mode: bool = False, aliases: list[str] | None = None):
    """Print shell completion setup instructions for the current shell."""
    import os
    shell = os.environ.get("SHELL", "/bin/bash")
    shell_name = Path(shell).name
    me = sys.argv[0]

    if eval_mode:
        _print_completion_code(shell_name, aliases)
        return

    print(f"# Shell completion for rossoctlx ({shell_name})")
    print()

    if shell_name in ("zsh", "bash"):
        rc_file = "~/.zshrc" if shell_name == "zsh" else "~/.bashrc"
        print("# Enable in current shell:")
        print(f'eval "$({me} completions --eval)"')
        print()
        print(f"# Or add to {rc_file} for persistence:")
        print(f'eval "$({me} completions --eval)"')
        print()
        print("# With alias (e.g. alias rx=rossoctlx.py):")
        print(f'eval "$({me} completions --eval --alias rx)"')
    elif shell_name == "fish":
        print("# Enable in current shell:")
        print(f"{me} completions --eval | source")
        print()
        print("# Or add to ~/.config/fish/completions/rossoctlx.fish:")
        print(f"{me} completions --eval | source")
    else:
        print(f"# No completion support for {shell_name}")
        print("# Supported shells: bash, zsh, fish")


def _log_file() -> Path:
    """Resolve log file path (same logic as rossocortex.py CONFIG_DIR)."""
    import os
    config = Path(os.environ.get("ROSSOCORTEX_CONFIG_DIR", Path.home() / ".config" / "rossocortex"))
    return config / "rossocortex.log"


def _print_banner():
    """Print compact status: version, upstream, budget, agents (< 10 lines)."""
    state = _load_state()
    mode = state.get("mode", "?")
    port = state.get("port", "?")
    pid = state.get("pid", "?")
    upstream = state.get("upstream", "?")
    budget = state.get("budget", "?")
    image = state.get("image", "")
    runtime_env = os.environ.get("ROSSOCORTEX_RUNTIME", "")
    runtime_flag = f" --runtime={runtime_env}" if runtime_env else ""
    me = sys.argv[0]

    print(f"rossocortex (pid={pid}, port={port}, mode={mode})")
    if image:
        print(f"  image={image}  upstream={upstream}  budget=${budget}/day")
    else:
        print(f"  upstream={upstream}  budget=${budget}/day")

    data = _load_agents()
    agents = data.get("agents", {})
    if agents:
        parts = []
        for name, info in agents.items():
            spend_data = _load_agent_spend(name)
            spent = spend_data.get("total_spend", 0.0)
            ab = info.get("budget")
            b = f"${ab:.0f}" if ab else "unlimited"
            parts.append(f"{name}(${spent:.2f}/{b})")
        print(f"  agents: {', '.join(parts)}")

    print(f"  Use: {me}{runtime_flag} log -f")


def _ensure_running() -> bool:
    """Ensure rossocortex is running. Start it if not. Returns True if running."""
    pid = _is_running()
    if pid:
        return True

    import subprocess as sp
    runtime = _find_container_runtime()
    if runtime:
        result = sp.run([runtime, "ps", "-q", "-f", f"name={CONTAINER_NAME}"], capture_output=True, text=True)
        if result.stdout.strip():
            return True

    state = _load_state()
    if state.get("control_port"):
        try:
            httpx.get(f"http://localhost:{state['control_port']}/version", timeout=2.0)
            return True
        except httpx.ConnectError:
            pass

    upstream = os.environ.get("ROSSOCORTEX_UPSTREAM") or os.environ.get("ANTHROPIC_BASE_URL") or state.get("upstream") or ""
    if not upstream:
        print("rossocortex is not running and no ROSSOCORTEX_UPSTREAM set — cannot auto-start.", file=sys.stderr)
        print("  Fix: export ROSSOCORTEX_UPSTREAM=https://your-litellm-proxy.example.com", file=sys.stderr)
        return False
    local_dir = os.environ.get("ROSSOCORTEX_CONTAINER_LOCAL_DIR")
    if local_dir:
        print(f"rossocortex is not running — starting (local: {local_dir})...")
        cmd_start(DEFAULT_PROXY_PORT, DEFAULT_PROXY_PORT + 1, upstream, 5.0, False)
    else:
        print("rossocortex is not running — starting (container)...")
        cmd_start_container(DEFAULT_PROXY_PORT, DEFAULT_PROXY_PORT + 1, upstream, 5.0, "quay.io/aslomnet/rosscortex:latest")
    return _is_running() is not None


def cmd_log(follow: bool = False, lines: int = 20, agent_filter: str | None = None):
    """Show rossocortex request log."""
    if not _ensure_running():
        return

    _print_banner()

    log_file = _log_file()
    if not log_file.exists():
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch()

    if follow:
        import subprocess as sp
        sys.stdout.flush()
        if log_file.stat().st_size == 0:
            print("(waiting for first log entry...)")
            sys.stdout.flush()
        cmd = ["tail", "-f", str(log_file)]
        if agent_filter:
            print(f"Following (filter: agent={agent_filter})...")
            try:
                proc = sp.Popen(cmd, stdout=sp.PIPE, text=True)
                for line in proc.stdout:
                    if f"agent={agent_filter}" in line:
                        print(line, end="")
            except KeyboardInterrupt:
                proc.terminate()
        else:
            try:
                sp.run(cmd)
            except KeyboardInterrupt:
                pass
        return

    all_lines = log_file.read_text().splitlines()
    if agent_filter:
        all_lines = [l for l in all_lines if f"agent={agent_filter}" in l]
    if not all_lines:
        print("(no log entries yet)")
        return
    for line in all_lines[-lines:]:
        print(line)


def main():
    parser = argparse.ArgumentParser(description="rossoctlx — manage a running rossocortex proxy")
    parser.add_argument("--control-url", default=DEFAULT_CONTROL_URL, help="Rossocortex control API URL")
    parser.add_argument("--runtime", choices=["docker", "podman"], default=None, help="Container runtime (default: auto-detect, or ROSSOCORTEX_RUNTIME env)")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("version", help="Show version and status of running rossocortex")
    subparsers.add_parser("status", help="Check if rossocortex is running")

    start_parser = subparsers.add_parser("start", help="Start rossocortex (container by default, --local for native)")
    start_parser.add_argument("--port", type=int, default=DEFAULT_PROXY_PORT, help="Proxy listen port")
    start_parser.add_argument("--control-port", type=int, default=8186, help="Control API port")
    start_parser.add_argument("--upstream", default="", help="Upstream LiteLLM URL")
    start_parser.add_argument("--budget", type=float, default=5.0, help="Global daily budget in USD")
    start_parser.add_argument("--local", action="store_true", help="Run locally (uses ROSSOCORTEX_CONTAINER_LOCAL_DIR or rossocortex-container/)")
    start_parser.add_argument("--no-authbridge", action="store_true", help="Direct mode without AuthBridge (local only)")
    start_parser.add_argument("--image", default="quay.io/aslomnet/rosscortex:latest", help="Container image (default mode)")
    start_parser.add_argument("--log-follow", "-f", action="store_true", dest="log_follow", help="After starting, follow the log (like 'start' then 'log -f')")

    subparsers.add_parser("stop", help="Stop running rossocortex daemon")

    log_parser = subparsers.add_parser("log", aliases=["logs"], help="Show rossocortex request log")
    log_parser.add_argument("-f", "--follow", action="store_true", help="Follow log output (like tail -f)")
    log_parser.add_argument("-n", "--lines", type=int, default=20, help="Number of lines to show (default: 20)")
    log_parser.add_argument("--agent", dest="log_agent", metavar="NAME", help="Filter to specific agent")

    subparsers.add_parser("agents", help="List all registered agents (shortcut for 'agent --list')")
    agent_parser = subparsers.add_parser("agent", help="Create or retrieve agent proxy credentials")
    agent_parser.add_argument("agent_name", nargs="?", help="Agent name to register/retrieve")
    agent_parser.add_argument("--list", action="store_true", help="List all registered agents")
    agent_parser.add_argument("--delete", action="store_true", help="Delete the named agent")
    agent_parser.add_argument("--budget", default=None, help="Daily budget in USD (or 'unlimited')")
    agent_parser.add_argument("--credentials", type=str, default=None, help="Path to agent-specific credentials dir (overrides shared)")
    agent_parser.add_argument("--network-allow", action="append", dest="network_allow", metavar="HOST", help="Allowed upstream hosts (repeatable, replaces existing list)")
    agent_parser.add_argument("--network-deny", action="append", dest="network_deny", metavar="HOST", help="Denied upstream hosts (repeatable, replaces existing list)")
    agent_parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT, help="Rossocortex proxy port")

    comp_parser = subparsers.add_parser("completions", help="Print shell completion setup for current $SHELL")
    comp_parser.add_argument("--eval", action="store_true", dest="eval_mode", help="Output raw completion code for eval")
    comp_parser.add_argument("--alias", action="append", dest="aliases", metavar="NAME", help="Also register completion for this alias (repeatable)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.runtime:
        os.environ["ROSSOCORTEX_RUNTIME"] = args.runtime

    control_url = args.control_url
    if control_url == DEFAULT_CONTROL_URL:
        state = _load_state()
        if state.get("control_port"):
            control_url = f"http://localhost:{state['control_port']}"

    if args.command == "status":
        cmd_status(control_url)
    elif args.command == "version":
        cmd_version(control_url)
    elif args.command == "start":
        local_dir = os.environ.get("ROSSOCORTEX_CONTAINER_LOCAL_DIR", "")
        if args.local:
            if not local_dir:
                print("ERROR: --local requires ROSSOCORTEX_CONTAINER_LOCAL_DIR to be set", file=sys.stderr)
                print(f"  export ROSSOCORTEX_CONTAINER_LOCAL_DIR=/path/to/rossocortex-container", file=sys.stderr)
                sys.exit(1)
            local_path = Path(local_dir)
            missing = []
            if not (local_path / "rossocortex.py").exists():
                missing.append("rossocortex.py")
            if not (local_path / "templates").is_dir():
                missing.append("templates/")
            if missing:
                print(f"ERROR: ROSSOCORTEX_CONTAINER_LOCAL_DIR={local_dir} is missing: {', '.join(missing)}", file=sys.stderr)
                sys.exit(1)
            cmd_start(args.port, args.control_port, args.upstream, args.budget, args.no_authbridge)
        else:
            cmd_start_container(args.port, args.control_port, args.upstream, args.budget, args.image)
        if args.log_follow:
            cmd_log(follow=True, lines=20, agent_filter=None)
    elif args.command == "stop":
        cmd_stop()
    elif args.command in ("log", "logs"):
        cmd_log(args.follow, args.lines, args.log_agent)
    elif args.command == "agents":
        cmd_agent_id(None, DEFAULT_PROXY_PORT, True, False, None, None, None, None)
    elif args.command == "agent":
        agent_budget = args.budget
        if agent_budget is not None:
            if agent_budget.lower() == "unlimited":
                agent_budget = 0  # 0 means remove budget cap
            else:
                try:
                    agent_budget = float(agent_budget)
                except ValueError:
                    print(f"ERROR: --budget must be a number or 'unlimited', got '{agent_budget}'", file=sys.stderr)
                    sys.exit(1)
        cmd_agent_id(args.agent_name, args.proxy_port, args.list, args.delete, agent_budget, args.credentials, args.network_allow, args.network_deny)
    elif args.command == "completions":
        cmd_completions(args.eval_mode, args.aliases)


if __name__ == "__main__":
    main()
