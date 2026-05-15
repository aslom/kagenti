 #!/usr/bin/env -S uv run --with openshell --with click
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openshell",
#     "click>=8.0",
# ]
# ///
"""kosh - Kagenti OpenShell CLI wrapper.

Proxies all openshell subcommands (gateway, sandbox, status, etc.) and adds
kagenti-specific commands like ``teleport``.

Usage:
    uv run kosh.py gateway status
    uv run kosh.py sandbox list
    uv run kosh.py teleport my-sandbox
"""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import click


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    cwd = kwargs.get("cwd")
    env_overrides = kwargs.get("env")
    parts = [shlex.join(cmd)]
    if cwd:
        parts.append(f"(cwd={cwd})")
    if env_overrides:
        diff = {k: v for k, v in env_overrides.items() if os.environ.get(k) != v}
        if diff:
            env_str = " ".join(f"{k}={v}" for k, v in sorted(diff.items()))
            parts.append(f"(env: {env_str})")
    click.echo(f"+ {' '.join(parts)}", err=True)
    kwargs.setdefault("stdin", sys.stdin)
    kwargs.setdefault("stdout", sys.stdout)
    kwargs.setdefault("stderr", sys.stderr)
    return subprocess.run(cmd, **kwargs)


def _find_openshell() -> str:
    workspace_bin = pathlib.Path(__file__).resolve().parent.parent.parent / ".local" / "bin" / "openshell"
    if workspace_bin.is_file() and os.access(workspace_bin, os.X_OK):
        return str(workspace_bin)
    path = shutil.which("openshell")
    if path:
        return path
    click.echo("error: 'openshell' CLI not found in PATH", err=True)
    click.echo("Install it with: uv tool install -U openshell", err=True)
    sys.exit(1)


OPENSHELL_PASSTHROUGH = [
    "sandbox",
    "gateway",
    "status",
    "forward",
    "logs",
    "policy",
    "settings",
    "provider",
    "inference",
    "doctor",
    "term",
    "ssh-proxy",
]


class KoshGroup(click.Group):
    """Click group that delegates unknown commands to the openshell CLI."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        if cmd_name in OPENSHELL_PASSTHROUGH:
            return _make_passthrough(cmd_name)
        return None

    def list_commands(self, ctx: click.Context) -> list[str]:
        native = super().list_commands(ctx)
        return sorted(set(native + OPENSHELL_PASSTHROUGH))

    def format_usage(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write("Usage: kosh <command> [args...]\n")

    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_paragraph()
        formatter.write("Kagenti OpenShell CLI — all openshell commands plus kagenti extras.\n")


def _make_passthrough(name: str) -> click.Command:
    @click.command(name, context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
    @click.pass_context
    def proxy(ctx: click.Context) -> None:
        openshell = _find_openshell()
        result = _run([openshell, name, *ctx.args])
        sys.exit(result.returncode)

    proxy.help = f"(passthrough) openshell {name}"
    return proxy


@click.group(cls=KoshGroup)
@click.version_option(version="0.1.0", prog_name="kosh")
def cli() -> None:
    """kosh - Kagenti OpenShell CLI."""


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
TELEPORT_SH = SCRIPT_DIR / "teleport.sh"
DEFAULT_MODEL = "aws/claude-opus-4-6"
WORKSPACE_ROOT = SCRIPT_DIR.parent.parent

DEFAULT_BINARIES = [
    "/usr/local/bin/claude",
    "/usr/bin/node",
    "/usr/local/bin/node",
    "/usr/bin/curl",
]

BUILTIN_PROFILES: dict[str, dict] = {
    "claude-infra": {
        "builtin": True,
        "description": "Claude Code infrastructure (Anthropic API, statsig, sentry)",
        "endpoints": [
            {"host": "api.anthropic.com", "port": 443},
            {"host": "statsig.anthropic.com", "port": 443},
            {"host": "sentry.io", "port": 443},
            {"host": "platform.claude.com", "port": 443},
        ],
    },
    "web-search": {
        "builtin": True,
        "description": "Search engines (Google, Bing, DuckDuckGo)",
        "endpoints": [
            {"host": "google.com", "port": 443},
            {"host": "*.google.com", "port": 443},
            {"host": "*.googleapis.com", "port": 443},
            {"host": "bing.com", "port": 443},
            {"host": "*.bing.com", "port": 443},
            {"host": "duckduckgo.com", "port": 443},
            {"host": "*.duckduckgo.com", "port": 443},
        ],
    },
    "dev-tools": {
        "builtin": True,
        "description": "Developer resources (GitHub, Stack Overflow, npm, PyPI, docs)",
        "endpoints": [
            {"host": "github.com", "port": 443},
            {"host": "*.github.com", "port": 443},
            {"host": "*.githubusercontent.com", "port": 443},
            {"host": "stackoverflow.com", "port": 443},
            {"host": "*.stackoverflow.com", "port": 443},
            {"host": "*.stackexchange.com", "port": 443},
            {"host": "npmjs.com", "port": 443},
            {"host": "*.npmjs.com", "port": 443},
            {"host": "pypi.org", "port": 443},
            {"host": "*.pypi.org", "port": 443},
            {"host": "*.readthedocs.io", "port": 443},
            {"host": "*.docs.rs", "port": 443},
        ],
    },
    "ibm-litellm": {
        "builtin": True,
        "description": "IBM LiteLLM proxy",
        "endpoints": [
            {"host": "ete-litellm.ai-models.vpc-int.res.ibm.com", "port": 443},
        ],
    },
}


@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]), default="zsh")
def completions(shell: str) -> None:
    """Generate shell completions for kosh.

    Prints a completion script to stdout. Add to your shell profile:

    \b
        # zsh — add to ~/.zshrc
        eval "$(kosh completions zsh)"
    \b
        # bash — add to ~/.bashrc
        eval "$(kosh completions bash)"
    \b
        # fish — add to fish config
        kosh completions fish | source
    """
    from click.shell_completion import get_completion_class

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        click.echo(f"error: unsupported shell: {shell}", err=True)
        sys.exit(1)
    comp = comp_cls(cli, {}, "kosh", "_KOSH_COMPLETE")
    click.echo(comp.source())


@cli.command()
@click.option("--directory", "-d", default=None, type=click.Path(exists=True, file_okay=False), help="Project directory to teleport (defaults to last local sandbox).")
@click.option("--openshell-bin", default=None, help="Path to openshell binary.")
@click.option("--xdg-config-home", default=None, help="Override XDG_CONFIG_HOME for gateway config.")
@click.option("--connect/--no-connect", default=False, help="Connect to the sandbox after setup.")
@click.option("--custom-image", is_flag=True, default=False, help="Build sandbox from Dockerfile.sandbox (requires Docker).")
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Claude model to set as ANTHROPIC_MODEL.")
@click.option("--allow-profile", multiple=True, help="Apply domain profile after teleport (repeatable).")
@click.option("--reapply-allowlist/--no-reapply-allowlist", default=True, help="Reapply saved allowlists from config.")
def teleport(directory: str | None, openshell_bin: str | None, xdg_config_home: str | None, connect: bool, custom_image: bool, model: str, allow_profile: tuple[str, ...], reapply_allowlist: bool) -> None:
    """Set up and sync a project into an OpenShell sandbox.

    Creates the litellm provider if needed, creates a sandbox named after the
    project directory, uploads local files, and configures .bashrc inside the
    sandbox. Delegates to teleport.sh.

    If no --directory is specified, uses the last local sandbox from kosh
    config. The directory basename is used as the openshell sandbox name.

    Examples:

    \b
        kosh teleport
        kosh teleport -d ~/projects/my-app
        kosh teleport --connect
        kosh teleport -d . --allow-profile dev-tools --allow-profile web-search
    """
    if not TELEPORT_SH.exists():
        click.echo(f"error: teleport.sh not found at {TELEPORT_SH}", err=True)
        sys.exit(1)

    if directory:
        cwd = directory
    else:
        config_dir = _kosh_config_dir()
        last = _load_last_sandbox(config_dir)
        if last and pathlib.Path(last).is_dir():
            cwd = last
            click.echo(f"Using last local sandbox: {cwd}")
        else:
            click.echo("error: no --directory specified and no last local sandbox found.", err=True)
            click.echo("Create one first: kosh local-sandbox create --name <name>", err=True)
            sys.exit(1)

    cwd_path = pathlib.Path(cwd).resolve()
    sandbox_name = cwd_path.name

    env = os.environ.copy()
    env["OPENSHELL_BIN"] = openshell_bin or _find_openshell()
    if xdg_config_home:
        env["XDG_CONFIG_HOME"] = xdg_config_home
    if custom_image:
        env["KOSH_CUSTOM_IMAGE"] = "1"
    if model != DEFAULT_MODEL:
        env["KOSH_MODEL"] = model

    click.echo(f"Teleporting '{sandbox_name}' from {cwd_path}")
    result = _run(["bash", str(TELEPORT_SH)], cwd=str(cwd_path), env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

    # Apply domain allowlist profiles after successful teleport
    config_dir = _kosh_config_dir()
    all_profiles = _read_profiles(config_dir)
    metadata = _read_metadata(config_dir)
    sb = metadata["sandboxes"].setdefault(sandbox_name, {})

    profiles_to_apply: list[str] = []
    if allow_profile:
        profiles_to_apply = list(allow_profile)
        applied = sb.setdefault("applied_profiles", [])
        for p in profiles_to_apply:
            if p not in applied:
                applied.append(p)
        _write_metadata(config_dir, metadata)
    elif reapply_allowlist:
        profiles_to_apply = sb.get("applied_profiles", [])

    if profiles_to_apply:
        endpoints: list[dict] = []
        for pname in profiles_to_apply:
            p = all_profiles.get(pname)
            if p:
                endpoints.extend(p.get("endpoints", []))
                click.echo(f"  Applying profile '{pname}' ({len(p.get('endpoints', []))} endpoints)")
            else:
                click.echo(f"  Warning: profile '{pname}' not found, skipping.", err=True)
        for ep in sb.get("allowed_domains", []):
            if ep not in endpoints:
                endpoints.append(ep)
        if endpoints:
            _apply_endpoints(sandbox_name, endpoints)

    if connect:
        openshell = openshell_bin or shutil.which("openshell") or os.path.expanduser("~/.local/bin/openshell")
        result = _run([openshell, "sandbox", "connect", sandbox_name], cwd=str(cwd_path), env=env)
        sys.exit(result.returncode)


SANDBOX_SH = SCRIPT_DIR / "sandbox.sh"


def _kosh_config_dir() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "kosh"


def _read_metadata(config_dir: pathlib.Path) -> dict:
    meta_file = config_dir / "metadata.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text())
    return {"sandboxes": {}}


def _write_metadata(config_dir: pathlib.Path, metadata: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    meta_file = config_dir / "metadata.json"
    meta_file.write_text(json.dumps(metadata, indent=2) + "\n")


def _save_last_sandbox(config_dir: pathlib.Path, sandbox_dir: pathlib.Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "last_local_sandbox").write_text(str(sandbox_dir) + "\n")


def _load_last_sandbox(config_dir: pathlib.Path) -> str | None:
    last_file = config_dir / "last_local_sandbox"
    if last_file.exists():
        return last_file.read_text().strip()
    return None


def _read_profiles(config_dir: pathlib.Path) -> dict[str, dict]:
    """Merge built-in profiles with user-defined profiles from profiles.json."""
    profiles = dict(BUILTIN_PROFILES)
    pfile = config_dir / "profiles.json"
    if pfile.exists():
        data = json.loads(pfile.read_text())
        for name, p in data.get("profiles", {}).items():
            if name not in BUILTIN_PROFILES:
                profiles[name] = p
    return profiles


def _write_profiles(config_dir: pathlib.Path, user_profiles: dict[str, dict]) -> None:
    """Write user-defined profiles to profiles.json."""
    config_dir.mkdir(parents=True, exist_ok=True)
    pfile = config_dir / "profiles.json"
    pfile.write_text(json.dumps({"version": 1, "profiles": user_profiles}, indent=2) + "\n")


def _resolve_sandbox_name(sandbox: str | None) -> str:
    """Resolve sandbox name from option or last-used metadata."""
    if sandbox:
        return sandbox
    config_dir = _kosh_config_dir()
    last = _load_last_sandbox(config_dir)
    if last:
        return pathlib.Path(last).name
    click.echo("error: no --sandbox specified and no last sandbox found.", err=True)
    sys.exit(1)


def _apply_endpoints(sandbox_name: str, endpoints: list[dict], binaries: list[str] | None = None, wait: bool = True) -> int:
    """Call openshell policy update to add endpoints to a sandbox. Returns exit code."""
    if not endpoints:
        return 0
    openshell = _find_openshell()
    bins = binaries or DEFAULT_BINARIES
    cmd = [openshell, "policy", "update", sandbox_name]
    for ep in endpoints:
        host = ep["host"]
        port = ep.get("port", 443)
        cmd.extend(["--add-endpoint", f"{host}:{port}"])
    for b in bins:
        cmd.extend(["--binary", b])
    if wait:
        cmd.append("--wait")
    result = _run(cmd)
    return result.returncode


def _run_sandbox_sh(sandbox_dir: pathlib.Path) -> None:
    if not SANDBOX_SH.exists():
        click.echo(f"error: sandbox.sh not found at {SANDBOX_SH}", err=True)
        sys.exit(1)
    result = _run(["bash", str(SANDBOX_SH), "zsh"], cwd=str(sandbox_dir))
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# kosh allow — domain allowlist management
# ---------------------------------------------------------------------------


@cli.group()
def allow() -> None:
    """Manage domain allowlists for OpenShell sandboxes."""


@allow.command("add")
@click.argument("domains", nargs=-1, required=False)
@click.option("--sandbox", "-s", default=None, help="Sandbox name (defaults to last used).")
@click.option("--port", "-p", default=443, type=int, show_default=True, help="Port to allow.")
@click.option("--binary", "-b", multiple=True, help="Binary paths (defaults to claude + node + curl).")
@click.option("--no-wait", is_flag=True, default=False, help="Don't wait for policy reload.")
@click.option("--no-save", is_flag=True, default=False, help="Don't persist domains to config.")
@click.option("--from-file", "-f", type=click.Path(exists=True), default=None, help="Read domains from file (one per line).")
@click.option("--from-json", "-j", type=click.Path(exists=False), default=None, help="Read from JSON (output of 'allow denied --json'). Use '-' for stdin.")
def allow_add(domains: tuple[str, ...], sandbox: str | None, port: int, binary: tuple[str, ...], no_wait: bool, no_save: bool, from_file: str | None, from_json: str | None) -> None:
    """Allow domains on a running sandbox.

    Calls openshell policy update to add endpoints. Saves domains to
    per-sandbox config by default so they can be reapplied later.

    Domains can be space-separated args, comma-separated, read from a file,
    or piped as JSON from 'kosh allow denied --json'.

    Examples:

    \b
        kosh allow add github.com stackoverflow.com
        kosh allow add github.com,stackoverflow.com,pypi.org
        kosh allow add --from-file domains.txt --sandbox test
        kosh allow denied --json | kosh allow add --from-json - --sandbox test
        kosh allow add --from-json denied.json
        kosh allow add github.com --sandbox test --port 443
    """
    import json as json_mod

    all_endpoints: list[dict] = []
    for d in domains:
        for part in d.split(","):
            part = part.strip()
            if part:
                all_endpoints.append({"host": part, "port": port})
    if from_file:
        with open(from_file) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    for part in line.split(","):
                        part = part.strip()
                        if part:
                            all_endpoints.append({"host": part, "port": port})
    if from_json:
        if from_json == "-":
            data = json_mod.load(sys.stdin)
        else:
            with open(from_json) as fh:
                data = json_mod.load(fh)
        if not isinstance(data, list):
            raise click.UsageError("--from-json expects a JSON array of {host, port} objects.")
        for item in data:
            host = item.get("host")
            p = item.get("port", 443)
            if host:
                all_endpoints.append({"host": host, "port": int(p)})
    if not all_endpoints:
        raise click.UsageError("Provide domains as arguments, comma-separated, via --from-file, or --from-json.")
    name = _resolve_sandbox_name(sandbox)
    endpoints = all_endpoints
    bins = list(binary) if binary else None
    rc = _apply_endpoints(name, endpoints, binaries=bins, wait=not no_wait)
    if rc != 0:
        sys.exit(rc)
    click.echo(f"Allowed {len(endpoints)} domain(s) on sandbox '{name}'.")
    if not no_save:
        config_dir = _kosh_config_dir()
        metadata = _read_metadata(config_dir)
        sb = metadata["sandboxes"].setdefault(name, {})
        existing = sb.setdefault("allowed_domains", [])
        for ep in endpoints:
            if ep not in existing:
                existing.append(ep)
        _write_metadata(config_dir, metadata)


@allow.command("list")
@click.option("--sandbox", "-s", default=None, help="Sandbox name (defaults to last used).")
def allow_list(sandbox: str | None) -> None:
    """Show allowed domains for a sandbox.

    Examples:

    \b
        kosh allow list
        kosh allow list --sandbox test
    """
    name = _resolve_sandbox_name(sandbox)
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    sb = metadata.get("sandboxes", {}).get(name, {})
    profiles = sb.get("applied_profiles", [])
    domains = sb.get("allowed_domains", [])

    click.echo(f"Sandbox: {name}")
    if profiles:
        click.echo(f"Profiles: {', '.join(profiles)}")
    if domains:
        click.echo("Domains:")
        for ep in domains:
            click.echo(f"  {ep['host']}:{ep.get('port', 443)}")
    if not profiles and not domains:
        click.echo("  (no saved allowlists)")


@allow.command("remove")
@click.argument("domains", nargs=-1, required=True)
@click.option("--sandbox", "-s", default=None, help="Sandbox name (defaults to last used).")
def allow_remove(domains: tuple[str, ...], sandbox: str | None) -> None:
    """Remove domains from saved config (does NOT revoke from running sandbox).

    OpenShell policy update is additive-only. This command removes domains
    from the stored config so they won't be reapplied on next reapply.
    """
    name = _resolve_sandbox_name(sandbox)
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    sb = metadata.get("sandboxes", {}).get(name, {})
    existing = sb.get("allowed_domains", [])
    removed = 0
    for d in domains:
        matches = [ep for ep in existing if ep["host"] == d]
        for m in matches:
            existing.remove(m)
            removed += 1
    _write_metadata(config_dir, metadata)
    click.echo(f"Removed {removed} domain(s) from saved config for '{name}'.")
    if removed:
        click.echo("Note: domains are NOT revoked from the running sandbox (policy is additive).")


@allow.command("reapply")
@click.option("--sandbox", "-s", default=None, help="Sandbox name (defaults to last used).")
def allow_reapply(sandbox: str | None) -> None:
    """Reapply all saved allowlists to a sandbox.

    Useful after recreating a sandbox — applies all stored profiles and
    individual domains.
    """
    name = _resolve_sandbox_name(sandbox)
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    sb = metadata.get("sandboxes", {}).get(name, {})
    all_profiles = _read_profiles(config_dir)

    endpoints: list[dict] = []
    for pname in sb.get("applied_profiles", []):
        profile = all_profiles.get(pname)
        if profile:
            endpoints.extend(profile["endpoints"])
            click.echo(f"  Profile '{pname}': {len(profile['endpoints'])} endpoint(s)")
    for ep in sb.get("allowed_domains", []):
        if ep not in endpoints:
            endpoints.append(ep)

    if not endpoints:
        click.echo(f"No saved allowlists for sandbox '{name}'.")
        return

    click.echo(f"Reapplying {len(endpoints)} endpoint(s) to '{name}'...")
    rc = _apply_endpoints(name, endpoints)
    if rc != 0:
        sys.exit(rc)
    click.echo("Done.")


@allow.command("denied")
@click.option("--sandbox", "-s", default=None, help="Sandbox name (defaults to last used).")
@click.option("--since", default="1h", help="How far back to look (e.g. 5m, 1h, 24h).")
@click.option("--apply", "do_apply", is_flag=True, default=False, help="Immediately allow all denied domains.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON list.")
def allow_denied(sandbox: str | None, since: str, do_apply: bool, as_json: bool) -> None:
    """Show domains denied by the sandbox proxy.

    Reads OCSF logs for DENIED network events and extracts unique
    host:port pairs. Use --apply to immediately allow them all.

    Examples:

    \b
        kosh allow denied --sandbox test
        kosh allow denied --since 24h --apply
        kosh allow denied --json | jq .
    """
    import json as json_mod
    import re

    name = _resolve_sandbox_name(sandbox)
    openshell = _find_openshell()
    result = subprocess.run(
        [openshell, "logs", name, "-n", "200", "--source", "sandbox", "--since", since],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        click.echo(f"error: failed to read logs: {result.stderr.strip()}", err=True)
        sys.exit(1)

    pattern = re.compile(r"DENIED\s+\S+\s+->\s+(\S+):(\d+)")
    denied: dict[str, int] = {}
    for line in result.stdout.splitlines():
        m = pattern.search(line)
        if m:
            host, port = m.group(1), int(m.group(2))
            key = f"{host}:{port}"
            denied[key] = denied.get(key, 0) + 1

    if not denied:
        click.echo(f"No denied connections found in last {since} for sandbox '{name}'.")
        return

    if as_json:
        items = [{"host": k.rsplit(":", 1)[0], "port": int(k.rsplit(":", 1)[1]), "count": v} for k, v in sorted(denied.items())]
        click.echo(json_mod.dumps(items, indent=2))
    else:
        click.echo(f"Denied domains (last {since}) for sandbox '{name}':\n")
        for key in sorted(denied):
            click.echo(f"  {key}  ({denied[key]}x)")

    if do_apply:
        endpoints = [{"host": k.rsplit(":", 1)[0], "port": int(k.rsplit(":", 1)[1])} for k in denied]
        click.echo(f"\nApplying {len(endpoints)} denied domain(s)...")
        rc = _apply_endpoints(name, endpoints)
        if rc != 0:
            sys.exit(rc)
        config_dir = _kosh_config_dir()
        metadata = _read_metadata(config_dir)
        sb = metadata["sandboxes"].setdefault(name, {})
        existing = sb.setdefault("allowed_domains", [])
        for ep in endpoints:
            if ep not in existing:
                existing.append(ep)
        _write_metadata(config_dir, metadata)
        click.echo("Done — denied domains are now allowed and saved.")


# --- kosh allow profile ---


@allow.group()
def profile() -> None:
    """Manage reusable domain profiles."""


@profile.command("list")
def profile_list() -> None:
    """List available profiles.

    Examples:

    \b
        kosh allow profile list
    """
    config_dir = _kosh_config_dir()
    all_profiles = _read_profiles(config_dir)
    name_w = max((len(n) for n in all_profiles), default=4)
    name_w = max(name_w, 4)
    click.echo(f"{'NAME':<{name_w}}  {'TYPE':<9}  {'DOMAINS':>7}  DESCRIPTION")
    for pname in sorted(all_profiles):
        p = all_profiles[pname]
        ptype = "built-in" if p.get("builtin") else "user"
        count = len(p.get("endpoints", []))
        desc = p.get("description", "")
        click.echo(f"{pname:<{name_w}}  {ptype:<9}  {count:>7}  {desc}")


@profile.command("show")
@click.argument("name")
def profile_show(name: str) -> None:
    """Show endpoints in a profile."""
    config_dir = _kosh_config_dir()
    all_profiles = _read_profiles(config_dir)
    p = all_profiles.get(name)
    if not p:
        click.echo(f"error: profile '{name}' not found.", err=True)
        sys.exit(1)
    ptype = "built-in" if p.get("builtin") else "user"
    click.echo(f"Profile: {name} ({ptype})")
    click.echo(f"Description: {p.get('description', '-')}")
    click.echo("Endpoints:")
    for ep in p.get("endpoints", []):
        click.echo(f"  {ep['host']}:{ep.get('port', 443)}")


@profile.command("apply")
@click.argument("name")
@click.option("--sandbox", "-s", default=None, help="Sandbox name (defaults to last used).")
def profile_apply(name: str, sandbox: str | None) -> None:
    """Apply a profile's domains to a running sandbox.

    Examples:

    \b
        kosh allow profile apply dev-tools --sandbox test
        kosh allow profile apply web-search
    """
    config_dir = _kosh_config_dir()
    all_profiles = _read_profiles(config_dir)
    p = all_profiles.get(name)
    if not p:
        click.echo(f"error: profile '{name}' not found. Use 'kosh allow profile list'.", err=True)
        sys.exit(1)
    sb_name = _resolve_sandbox_name(sandbox)
    endpoints = p.get("endpoints", [])
    binaries = p.get("binaries", DEFAULT_BINARIES)
    click.echo(f"Applying profile '{name}' ({len(endpoints)} endpoints) to '{sb_name}'...")
    rc = _apply_endpoints(sb_name, endpoints, binaries=binaries)
    if rc != 0:
        sys.exit(rc)
    metadata = _read_metadata(config_dir)
    sb = metadata["sandboxes"].setdefault(sb_name, {})
    applied = sb.setdefault("applied_profiles", [])
    if name not in applied:
        applied.append(name)
    _write_metadata(config_dir, metadata)
    click.echo("Done.")


@profile.command("create")
@click.argument("name")
@click.option("--domain", "-d", multiple=True, required=True, help="Domain (host or host:port).")
@click.option("--description", default="", help="Profile description.")
def profile_create(name: str, domain: tuple[str, ...], description: str) -> None:
    """Create a user-defined profile.

    Examples:

    \b
        kosh allow profile create my-apis -d api.corp.com -d ml.corp.com:8443
    """
    if name in BUILTIN_PROFILES:
        click.echo(f"error: '{name}' is a built-in profile and cannot be overwritten.", err=True)
        sys.exit(1)
    endpoints = []
    for d in domain:
        if ":" in d and not d.startswith("*"):
            parts = d.rsplit(":", 1)
            endpoints.append({"host": parts[0], "port": int(parts[1])})
        else:
            endpoints.append({"host": d, "port": 443})
    config_dir = _kosh_config_dir()
    pfile = config_dir / "profiles.json"
    user_profiles: dict[str, dict] = {}
    if pfile.exists():
        user_profiles = json.loads(pfile.read_text()).get("profiles", {})
    user_profiles[name] = {"description": description, "endpoints": endpoints, "binaries": DEFAULT_BINARIES}
    _write_profiles(config_dir, user_profiles)
    click.echo(f"Created profile '{name}' with {len(endpoints)} endpoint(s).")


@profile.command("delete")
@click.argument("name")
def profile_delete(name: str) -> None:
    """Delete a user-defined profile."""
    if name in BUILTIN_PROFILES:
        click.echo(f"error: cannot delete built-in profile '{name}'.", err=True)
        sys.exit(1)
    config_dir = _kosh_config_dir()
    pfile = config_dir / "profiles.json"
    if not pfile.exists():
        click.echo(f"error: profile '{name}' not found.", err=True)
        sys.exit(1)
    user_profiles = json.loads(pfile.read_text()).get("profiles", {})
    if name not in user_profiles:
        click.echo(f"error: profile '{name}' not found.", err=True)
        sys.exit(1)
    del user_profiles[name]
    _write_profiles(config_dir, user_profiles)
    click.echo(f"Deleted profile '{name}'.")


# ---------------------------------------------------------------------------
# kosh local-sandbox
# ---------------------------------------------------------------------------


@cli.group("local-sandbox")
def local_sandbox() -> None:
    """Manage local macOS sandboxed environments."""



@local_sandbox.command()
@click.option("--name", required=True, help="Name for the local sandbox (used as directory name).")
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Claude model to set as ANTHROPIC_MODEL.")
def create(name: str, model: str) -> None:
    """Create a local sandbox directory and launch a sandboxed shell.

    Creates the directory in the current working directory if it doesn't
    exist, registers it (with full path) in kosh metadata, and starts
    sandbox.sh zsh inside it.

    Examples:

        kosh local-sandbox create --name my-project
    """
    if not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        click.echo("error: ANTHROPIC_AUTH_TOKEN is not set.", err=True)
        click.echo("Export it before running this command:", err=True)
        click.echo("  export ANTHROPIC_AUTH_TOKEN=<your-token>", err=True)
        sys.exit(1)

    sandbox_dir = (pathlib.Path.cwd() / name).resolve()

    if not sandbox_dir.exists():
        click.echo(f"Creating directory {sandbox_dir}")
        sandbox_dir.mkdir(parents=True)
    else:
        click.echo(f"Directory {sandbox_dir} already exists.")

    rc_lines = [
        'export ANTHROPIC_BASE_URL="https://ete-litellm.ai-models.vpc-int.res.ibm.com"',
        "export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1",
        f'export ANTHROPIC_MODEL="{model}"',
    ]
    rc_content = "\n".join(rc_lines) + "\n"
    for rc_name in (".bashrc", ".zshrc"):
        rc_path = sandbox_dir / rc_name
        if not rc_path.exists():
            rc_path.write_text(rc_content)
            click.echo(f"Created {rc_path}")
        else:
            existing = rc_path.read_text()
            added = False
            for line in rc_lines:
                key = line.split("=")[0]
                if key not in existing:
                    with rc_path.open("a") as f:
                        f.write(line + "\n")
                    added = True
                elif "ANTHROPIC_MODEL" in key and f'"{model}"' not in existing:
                    new_text = "\n".join(
                        (line if l.startswith("export ANTHROPIC_MODEL=") else l)
                        for l in existing.splitlines()
                    ) + "\n"
                    rc_path.write_text(new_text)
                    added = True
            if added:
                click.echo(f"Updated {rc_path}")
            else:
                click.echo(f"{rc_path} already configured.")

    config_dir = _kosh_config_dir()

    metadata = _read_metadata(config_dir)
    if name not in metadata["sandboxes"]:
        metadata["sandboxes"][name] = {"directory": str(sandbox_dir)}
        _write_metadata(config_dir, metadata)
        click.echo(f"Registered sandbox '{name}' in {config_dir / 'metadata.json'}")
    else:
        click.echo(f"Sandbox '{name}' already registered.")

    _save_last_sandbox(config_dir, sandbox_dir)
    click.echo(f"Saved as last sandbox in {config_dir / 'last_local_sandbox'}")

    _run_sandbox_sh(sandbox_dir)


@local_sandbox.command()
@click.option("--name", default=None, help="Name of the local sandbox to connect to (defaults to last used).")
def connect(name: str | None) -> None:
    """Connect to an existing local sandbox.

    If --name is omitted, reconnects to the last sandbox used. Launches
    sandbox.sh zsh in the sandbox directory.

    Examples:

        kosh local-sandbox connect
        kosh local-sandbox connect --name my-project
    """
    config_dir = _kosh_config_dir()

    if name:
        metadata = _read_metadata(config_dir)
        entry = metadata.get("sandboxes", {}).get(name)
        if entry:
            sandbox_dir = pathlib.Path(entry["directory"])
        else:
            sandbox_base = pathlib.Path(os.environ.get("SANDBOX_DIR", pathlib.Path.home() / "sandbox"))
            sandbox_dir = (sandbox_base / name).resolve()
    else:
        last = _load_last_sandbox(config_dir)
        if not last:
            click.echo("error: no last sandbox found. Use --name or create one first.", err=True)
            sys.exit(1)
        sandbox_dir = pathlib.Path(last)

    if not sandbox_dir.is_dir():
        click.echo(f"error: sandbox directory does not exist: {sandbox_dir}", err=True)
        sys.exit(1)

    _save_last_sandbox(config_dir, sandbox_dir)
    click.echo(f"Connecting to local sandbox at {sandbox_dir}")

    _run_sandbox_sh(sandbox_dir)


@local_sandbox.command("list")
def list_sandboxes() -> None:
    """List all registered local sandboxes.

    Examples:

        kosh local-sandbox list
    """
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    sandboxes = metadata.get("sandboxes", {})
    last = _load_last_sandbox(config_dir)

    if not sandboxes:
        click.echo("No local sandboxes registered.")
        return

    name_w = max(len(n) for n in sandboxes)
    name_w = max(name_w, 4)
    click.echo(f"{'NAME':<{name_w}}  {'STATUS':<9}  DIRECTORY")
    for name, entry in sorted(sandboxes.items()):
        directory = entry.get("directory", "")
        exists = pathlib.Path(directory).is_dir()
        status = "exists" if exists else "missing"
        marker = " *" if last and pathlib.Path(last) == pathlib.Path(directory) else ""
        click.echo(f"{name:<{name_w}}  {status:<9}  {directory}{marker}")

    if last:
        click.echo(f"\n* = last used")


@local_sandbox.command()
@click.option("--name", required=True, help="Name of the local sandbox to delete.")
def delete(name: str) -> None:
    """Delete a local sandbox directory and remove it from metadata.

    Examples:

        kosh local-sandbox delete --name my-project
    """
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    entry = metadata.get("sandboxes", {}).get(name)

    if entry:
        sandbox_dir = pathlib.Path(entry["directory"])
    else:
        sandbox_base = pathlib.Path(os.environ.get("SANDBOX_DIR", pathlib.Path.home() / "sandbox"))
        sandbox_dir = (sandbox_base / name).resolve()

    if sandbox_dir.is_dir():
        click.confirm(f"Delete directory {sandbox_dir} and all its contents?", abort=True)
        import shutil as _shutil
        _shutil.rmtree(sandbox_dir)
        click.echo(f"Deleted {sandbox_dir}")
    else:
        click.echo(f"Directory {sandbox_dir} does not exist (skipping).")

    if name in metadata.get("sandboxes", {}):
        del metadata["sandboxes"][name]
        _write_metadata(config_dir, metadata)
        click.echo(f"Removed '{name}' from {config_dir / 'metadata.json'}")

    last = _load_last_sandbox(config_dir)
    if last and pathlib.Path(last) == sandbox_dir:
        (config_dir / "last_local_sandbox").unlink(missing_ok=True)
        click.echo("Cleared last_local_sandbox (was pointing to deleted sandbox).")

    click.echo(f"Sandbox '{name}' deleted.")


if __name__ == "__main__":
    cli(prog_name="kosh")
